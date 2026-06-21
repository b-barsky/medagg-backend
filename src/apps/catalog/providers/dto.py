from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderDatasetSummary:
    external_id: str
    source_url: str
    title: str

    owner_name: str = ""
    owner_ref: str = ""

    total_bytes: int | None = None
    download_count: int | None = None
    vote_count: int | None = None
    view_count: int | None = None
    usability_rating: Decimal | None = None

    remote_version: str = ""
    remote_updated_at: datetime | None = None

    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderDatasetDetails:
    external_id: str

    title: str | None = None
    subtitle: str | None = None
    description: str | None = None

    # None means that the detail endpoint omitted the field. An empty tuple
    # means that it explicitly returned no licenses.
    license_names: tuple[str, ...] | None = None
    is_private: bool | None = None
    thumbnail_url: str | None = None

    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderSearchPage:
    query: str
    page: int
    items: tuple[ProviderDatasetSummary, ...]
