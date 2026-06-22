import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from apps.catalog.models import SourceDataset


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    return str(value)


def canonical_json(value: object) -> str:
    """Serialize a value deterministically for fingerprints and revisions."""

    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def source_dataset_revision(source_dataset: SourceDataset) -> str:
    """
    Return a stable revision for the remote dataset represented locally.

    Provider version and update timestamps are preferred because they identify
    the downloadable revision directly. Metadata is used only as a fallback
    for providers that expose neither value.
    """

    payload: dict[str, Any] = {
        "source": source_dataset.source.slug,
        "external_id": source_dataset.external_id,
        "remote_version": source_dataset.remote_version or "",
        "remote_updated_at": (
            source_dataset.remote_updated_at.isoformat()
            if source_dataset.remote_updated_at
            else None
        ),
    }

    if not payload["remote_version"] and not payload["remote_updated_at"]:
        payload["metadata"] = (
            source_dataset.detail_metadata
            or source_dataset.summary_metadata
            or {}
        )

    return sha256_json(payload)


def import_license_fingerprint(
    source_dataset: SourceDataset,
    *,
    source_revision: str,
    canonical_licenses: Sequence[str],
) -> str:
    """Fingerprint the exact access and license facts accepted by a user."""

    payload: Mapping[str, object] = {
        "source": source_dataset.source.slug,
        "external_id": source_dataset.external_id,
        "source_revision": source_revision,
        "is_private": source_dataset.is_private,
        "license_names": sorted(
            str(value).strip()
            for value in source_dataset.license_names
            if str(value).strip()
        ),
        "license_name": source_dataset.license_name.strip(),
        "canonical_licenses": sorted(canonical_licenses),
    }

    return sha256_json(payload)
