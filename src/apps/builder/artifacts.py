from __future__ import annotations

import hashlib
import shutil
import stat
import tarfile
import tempfile
import zipfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.datasets.models import DatasetArtifact, DatasetArtifactKind, DatasetVersion
from apps.datasets.storage import S3ObjectStorage


class ArtifactMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedDataset:
    root: Path
    files: tuple[Path, ...]
    artifact: DatasetArtifact


class ArtifactMaterializer(AbstractContextManager[MaterializedDataset]):
    """Download, verify and safely unpack one immutable dataset artifact."""

    def __init__(
        self,
        dataset_version: DatasetVersion,
        *,
        storage: S3ObjectStorage | None = None,
    ) -> None:
        self.dataset_version = dataset_version
        self.storage = storage or S3ObjectStorage()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._materialized: MaterializedDataset | None = None

    def __enter__(self) -> MaterializedDataset:
        artifact = self._select_artifact()
        self._temporary = tempfile.TemporaryDirectory(prefix="medagg-builder-")
        base = Path(self._temporary.name).resolve()
        downloaded = base / "download" / artifact.filename
        downloaded.parent.mkdir(parents=True, exist_ok=True)
        arguments: dict[str, object] = {}
        if artifact.object_version_id:
            arguments["ExtraArgs"] = {"VersionId": artifact.object_version_id}
        try:
            self.storage.client.download_file(
                artifact.bucket,
                artifact.object_key,
                str(downloaded),
                **arguments,
            )
        except Exception as exc:  # boto3 exposes several transport exceptions
            self._cleanup()
            raise ArtifactMaterializationError(
                f"Could not download artifact '{artifact.filename}'."
            ) from exc

        self._verify(downloaded, artifact)
        root = base / "content"
        root.mkdir(parents=True, exist_ok=True)
        try:
            self._unpack(downloaded, root)
        except Exception:
            self._cleanup()
            raise

        files = tuple(
            sorted(
                path
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
        )
        self._materialized = MaterializedDataset(
            root=root,
            files=files,
            artifact=artifact,
        )
        return self._materialized

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._cleanup()

    def _select_artifact(self) -> DatasetArtifact:
        artifacts = self.dataset_version.artifacts.all()
        artifact = artifacts.filter(kind=DatasetArtifactKind.DATA).first()
        if artifact is None:
            artifact = artifacts.filter(
                kind=DatasetArtifactKind.SOURCE_ARCHIVE
            ).first()
        if artifact is None:
            raise ArtifactMaterializationError(
                "The dataset version has no analyzable data artifact."
            )
        return artifact

    @staticmethod
    def _verify(path: Path, artifact: DatasetArtifact) -> None:
        if path.stat().st_size != artifact.size_bytes:
            raise ArtifactMaterializationError(
                "Downloaded artifact size does not match the database record."
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != artifact.checksum_sha256.lower():
            raise ArtifactMaterializationError(
                "Downloaded artifact failed SHA-256 validation."
            )

    def _unpack(self, source: Path, target: Path) -> None:
        lowered = source.name.lower()
        if zipfile.is_zipfile(source):
            self._extract_zip(source, target)
            return
        if tarfile.is_tarfile(source):
            self._extract_tar(source, target)
            return
        if lowered.endswith((".csv.gz", ".json.gz", ".jsonl.gz")):
            shutil.copy2(source, target / source.name)
            return
        shutil.copy2(source, target / source.name)

    @staticmethod
    def _safe_destination(root: Path, member_name: str) -> Path:
        normalized = member_name.replace("\\", "/")
        if normalized.startswith("/"):
            raise ArtifactMaterializationError("Archive contains an absolute path.")
        destination = (root / normalized).resolve()
        try:
            destination.relative_to(root.resolve())
        except ValueError as exc:
            raise ArtifactMaterializationError(
                "Archive contains a path traversal entry."
            ) from exc
        return destination

    def _extract_zip(self, source: Path, target: Path) -> None:
        total = 0
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            self._validate_member_count(len(members))
            for member in members:
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArtifactMaterializationError(
                        "Archive symbolic links are not allowed."
                    )
                destination = self._safe_destination(target, member.filename)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.file_size
                self._validate_expanded_bytes(total)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, destination.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)

    def _extract_tar(self, source: Path, target: Path) -> None:
        total = 0
        with tarfile.open(source, mode="r:*") as archive:
            members = archive.getmembers()
            self._validate_member_count(len(members))
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise ArtifactMaterializationError(
                        "Archive links and device files are not allowed."
                    )
                destination = self._safe_destination(target, member.name)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                total += member.size
                self._validate_expanded_bytes(total)
                incoming = archive.extractfile(member)
                if incoming is None:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with incoming, destination.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)

    @staticmethod
    def _validate_member_count(count: int) -> None:
        if count > settings.BUILDER_MAX_ARCHIVE_FILES:
            raise ArtifactMaterializationError(
                "Archive contains more files than the configured limit."
            )

    @staticmethod
    def _validate_expanded_bytes(total: int) -> None:
        if total > settings.BUILDER_MAX_EXPANDED_BYTES:
            raise ArtifactMaterializationError(
                "Expanded archive exceeds the configured byte limit."
            )

    def _cleanup(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._materialized = None
