import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from boto3.s3.transfer import TransferConfig
from django.conf import settings


class ObjectStorageError(RuntimeError):
    """An S3-compatible storage operation failed."""


class ObjectStorageCollisionError(ObjectStorageError):
    """An existing object key does not contain the expected bytes."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    object_key: str
    object_version_id: str
    etag: str
    size_bytes: int
    checksum_sha256: str
    reused: bool


class S3ObjectStorage:
    """Small S3/MinIO adapter used by dataset ingestion."""

    def __init__(
        self,
        *,
        client: BaseClient | None = None,
        public_client: BaseClient | None = None,
    ) -> None:
        self._client = client
        self._public_client = public_client
        self._transfer_config = TransferConfig(
            multipart_threshold=16 * 1024 * 1024,
            multipart_chunksize=16 * 1024 * 1024,
            max_concurrency=4,
            use_threads=True,
        )

    @property
    def client(self) -> BaseClient:
        if self._client is None:
            self._client = self._build_client(
                settings.OBJECT_STORAGE_ENDPOINT_URL
            )

        return self._client

    @property
    def public_client(self) -> BaseClient:
        if self._public_client is None:
            self._public_client = self._build_client(
                settings.OBJECT_STORAGE_PUBLIC_ENDPOINT_URL
            )

        return self._public_client

    @staticmethod
    def _build_client(endpoint_url: str | None) -> BaseClient:
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=settings.OBJECT_STORAGE_REGION,
            aws_access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=settings.OBJECT_STORAGE_SECRET_KEY,
            config=Config(
                signature_version="s3v4",
                connect_timeout=(
                    settings.OBJECT_STORAGE_CONNECT_TIMEOUT_SECONDS
                ),
                read_timeout=(
                    settings.OBJECT_STORAGE_READ_TIMEOUT_SECONDS
                ),
                retries={
                    "max_attempts": settings.OBJECT_STORAGE_MAX_ATTEMPTS,
                    "mode": "standard",
                },
                s3={
                    "addressing_style": (
                        settings.OBJECT_STORAGE_ADDRESSING_STYLE
                    )
                },
            ),
        )

    def ensure_bucket(self) -> None:
        bucket = settings.OBJECT_STORAGE_BUCKET

        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))

            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise ObjectStorageError(
                    f"Could not inspect object-storage bucket '{bucket}'."
                ) from exc

            create_arguments: dict[str, Any] = {"Bucket": bucket}

            if settings.OBJECT_STORAGE_REGION != "us-east-1":
                create_arguments["CreateBucketConfiguration"] = {
                    "LocationConstraint": settings.OBJECT_STORAGE_REGION,
                }

            try:
                self.client.create_bucket(**create_arguments)
            except ClientError as create_exc:
                create_code = str(
                    create_exc.response.get("Error", {}).get("Code", "")
                )

                if create_code != "BucketAlreadyOwnedByYou":
                    raise ObjectStorageError(
                        "Could not create object-storage bucket "
                        f"'{bucket}'."
                    ) from create_exc
            except BotoCoreError as create_exc:
                raise ObjectStorageError(
                    f"Could not create object-storage bucket '{bucket}'."
                ) from create_exc
        except BotoCoreError as exc:
            raise ObjectStorageError(
                f"Could not inspect object-storage bucket '{bucket}'."
            ) from exc

        try:
            self.client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError(
                f"Could not enable versioning for bucket '{bucket}'."
            ) from exc

    def store_file(
        self,
        path: Path,
        *,
        dataset_id: int,
        filename: str,
        content_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> StoredObject:
        resolved_path = path.resolve()

        if not resolved_path.is_file():
            raise ObjectStorageError(
                f"Artifact does not exist: {resolved_path}"
            )

        if resolved_path.stat().st_size != size_bytes:
            raise ObjectStorageError(
                "Artifact size changed before object-storage upload."
            )

        safe_filename = self._safe_filename(filename)
        object_key = (
            f"datasets/{dataset_id}/sha256/{checksum_sha256[:2]}/"
            f"{checksum_sha256}/{safe_filename}"
        )
        existing = self._head_or_none(object_key)

        if existing is not None:
            return self._validate_existing(
                object_key=object_key,
                response=existing,
                size_bytes=size_bytes,
                checksum_sha256=checksum_sha256,
            )

        metadata = {
            "sha256": checksum_sha256,
            "dataset-id": str(dataset_id),
            "original-filename": safe_filename,
        }

        try:
            self.client.upload_file(
                str(resolved_path),
                settings.OBJECT_STORAGE_BUCKET,
                object_key,
                ExtraArgs={
                    "ContentType": (
                        content_type or "application/octet-stream"
                    ),
                    "Metadata": metadata,
                },
                Config=self._transfer_config,
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ObjectStorageError(
                f"Could not upload artifact '{safe_filename}'."
            ) from exc

        response = self._head_required(object_key)
        stored = self._validate_existing(
            object_key=object_key,
            response=response,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
        )

        return StoredObject(
            bucket=stored.bucket,
            object_key=stored.object_key,
            object_version_id=stored.object_version_id,
            etag=stored.etag,
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.checksum_sha256,
            reused=False,
        )

    def presigned_download_url(
        self,
        *,
        bucket: str,
        object_key: str,
        object_version_id: str = "",
        filename: str,
    ) -> str:
        normalized_bucket = bucket.strip()

        if not normalized_bucket:
            raise ObjectStorageError(
                "Artifact storage bucket cannot be blank."
            )

        parameters: dict[str, str] = {
            "Bucket": normalized_bucket,
            "Key": object_key,
            "ResponseContentDisposition": (
                f'attachment; filename="{self._safe_filename(filename)}"'
            ),
        }

        if object_version_id:
            parameters["VersionId"] = object_version_id

        try:
            return self.public_client.generate_presigned_url(
                "get_object",
                Params=parameters,
                ExpiresIn=(
                    settings.OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS
                ),
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError(
                "Could not generate an artifact download URL."
            ) from exc

    def delete_object(
        self,
        *,
        object_key: str,
        object_version_id: str = "",
    ) -> None:
        arguments: dict[str, str] = {
            "Bucket": settings.OBJECT_STORAGE_BUCKET,
            "Key": object_key,
        }

        if object_version_id:
            arguments["VersionId"] = object_version_id

        try:
            self.client.delete_object(**arguments)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageError(
                f"Could not delete orphaned object '{object_key}'."
            ) from exc

    def _head_or_none(
        self,
        object_key: str,
    ) -> dict[str, Any] | None:
        try:
            return self.client.head_object(
                Bucket=settings.OBJECT_STORAGE_BUCKET,
                Key=object_key,
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))

            if code in {"404", "NoSuchKey", "NotFound"}:
                return None

            raise ObjectStorageError(
                f"Could not inspect object '{object_key}'."
            ) from exc
        except BotoCoreError as exc:
            raise ObjectStorageError(
                f"Could not inspect object '{object_key}'."
            ) from exc

    def _head_required(
        self,
        object_key: str,
    ) -> dict[str, Any]:
        response = self._head_or_none(object_key)

        if response is None:
            raise ObjectStorageError(
                "The object was not visible after a successful upload."
            )

        return response

    @staticmethod
    def _validate_existing(
        *,
        object_key: str,
        response: dict[str, Any],
        size_bytes: int,
        checksum_sha256: str,
    ) -> StoredObject:
        metadata = response.get("Metadata") or {}
        stored_checksum = str(metadata.get("sha256", "")).lower()
        stored_size = int(response.get("ContentLength", -1))

        if (
            stored_size != size_bytes
            or stored_checksum != checksum_sha256.lower()
        ):
            raise ObjectStorageCollisionError(
                "An object already exists at the content-addressed key but "
                "its size or SHA-256 metadata does not match."
            )

        return StoredObject(
            bucket=settings.OBJECT_STORAGE_BUCKET,
            object_key=object_key,
            object_version_id=str(response.get("VersionId") or ""),
            etag=str(response.get("ETag") or "").strip('"'),
            size_bytes=stored_size,
            checksum_sha256=stored_checksum,
            reused=True,
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        # Treat both POSIX and Windows separators as path separators, then
        # restrict the result to safe ASCII for S3 keys and HTTP headers.
        candidate = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
        candidate = candidate.strip()
        candidate = "".join(
            character if 32 <= ord(character) < 127 else "_"
            for character in candidate
        )
        candidate = re.sub(
            r"[^A-Za-z0-9._() -]+",
            "_",
            candidate,
        )
        candidate = re.sub(r"\s+", " ", candidate).strip(" .")

        if not candidate or candidate in {".", ".."}:
            return "dataset-artifact.bin"

        return candidate[:240]
