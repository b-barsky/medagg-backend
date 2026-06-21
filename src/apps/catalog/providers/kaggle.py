import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from .base import DatasetProvider
from .dto import (ProviderDatasetSummary, ProviderDatasetDetails, ProviderSearchPage)
from .exceptions import (ProviderAuthenticationError, ProviderConfigurationError, ProviderResponseError,
                         ProviderUnavailableError)


class KaggleApiClient(Protocol):
    def dataset_list(self, *, search: str, page: int) -> list[Any | None] | None:
        ...

    def dataset_metadata(self, dataset: str, path: str) -> str:
        ...


class KaggleProvider(DatasetProvider):
    slug = "kaggle"

    def __init__(self, api: KaggleApiClient | None = None) -> None:
        self._api = (api if api is not None else self._build_client())

    @staticmethod
    def _build_client() -> KaggleApiClient:
        """
        Import Kaggle only when this provider is used.

        Importing the package initializes its global API client and may
        authenticate, so Django checks and unit-test discovery must not
        depend on live credentials.
        """

        try:
            import kaggle
        except SystemExit as exc:
            raise ProviderAuthenticationError("Kaggle authentication failed. Configure "
                                              "KAGGLE_API_TOKEN or a Kaggle token file.") from exc
        except ImportError as exc:
            raise ProviderConfigurationError("The 'kaggle' package is not installed.") from exc
        except Exception as exc:
            raise ProviderAuthenticationError("Kaggle API initialization failed.") from exc

        api = getattr(kaggle, "api", None)

        if api is None:
            raise ProviderConfigurationError("The installed Kaggle package does not "
                                             "expose kaggle.api.")

        return api

    def search_summary(self, query: str, *, page: int = 1) -> ProviderSearchPage:
        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("Search query cannot be blank.")

        if page < 1:
            raise ValueError("Page must be greater than or equal to 1.")

        try:
            results = self._api.dataset_list(search=normalized_query, page=page) or []
        except Exception as exc:
            raise ProviderUnavailableError("Kaggle dataset search failed.") from exc

        items = tuple(self._to_summary(dataset) for dataset in results if dataset is not None)

        return ProviderSearchPage(query=normalized_query, page=page, items=items)

    def fetch_details(self, external_id: str) -> ProviderDatasetDetails:
        normalized_external_id = external_id.strip().lower()

        if not normalized_external_id:
            raise ValueError("External dataset ID cannot be blank.")

        try:
            with TemporaryDirectory(prefix="medagg-kaggle-") as directory:
                metadata_path = self._api.dataset_metadata(normalized_external_id, directory)
                raw_text = Path(metadata_path).read_text(encoding="utf-8")
        except Exception as exc:
            raise ProviderUnavailableError("Kaggle metadata retrieval failed for "
                                           f"'{normalized_external_id}'.") from exc

        try:
            payload = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("Kaggle returned invalid metadata JSON for "
                                        f"'{normalized_external_id}'.") from exc

        if not isinstance(payload, dict):
            raise ProviderResponseError("Kaggle metadata must be a JSON object for "
                                        f"'{normalized_external_id}'.")

        info_candidate = payload.get("info")
        info: Mapping[str, Any]

        if info_candidate is None:
            info = payload
        elif isinstance(info_candidate, Mapping):
            info = info_candidate
        else:
            raise ProviderResponseError("Kaggle metadata field 'info' must be an object for "
                                        f"'{normalized_external_id}'.")

        return ProviderDatasetDetails(external_id=normalized_external_id, title=self._optional_text(info.get("title")),
                                      subtitle=self._optional_text(info.get("subtitle")),
                                      description=self._optional_text(info.get("description")),
                                      license_names=self._optional_license_names(info.get("licenses")),
                                      is_private=self._optional_boolean(info.get("isPrivate")),
                                      thumbnail_url=self._optional_text(
                                          info.get("image") or info.get("thumbnailImageUrl") or info.get(
                                              "thumbnailUrl")), metadata=payload)

    @classmethod
    def _to_summary(cls, dataset: Any) -> ProviderDatasetSummary:
        external_id = cls._first_text(dataset, "ref").lower()

        if not external_id:
            raise ProviderResponseError("Kaggle returned a dataset without a reference.")

        source_url = cls._first_text(dataset, "url")

        if not source_url:
            source_url = ("https://www.kaggle.com/datasets/"
                          f"{external_id}")

        owner_ref = cls._first_text(dataset, "owner_ref", "ownerRef")

        if not owner_ref:
            owner_ref = external_id.partition("/")[0]

        owner_name = cls._first_text(dataset, "owner_name", "ownerName", "creator_name", "creatorName")

        if not owner_name:
            owner_name = owner_ref

        version = cls._get(dataset, "current_version_number", "currentVersionNumber")

        return ProviderDatasetSummary(external_id=external_id, source_url=source_url,
                                      title=(cls._first_text(dataset, "title") or external_id), owner_name=owner_name,
                                      owner_ref=owner_ref,
                                      total_bytes=cls._optional_int(cls._get(dataset, "total_bytes", "totalBytes")),
                                      download_count=cls._optional_int(
                                          cls._get(dataset, "download_count", "downloadCount")),
                                      vote_count=cls._optional_int(cls._get(dataset, "vote_count", "voteCount")),
                                      view_count=cls._optional_int(cls._get(dataset, "view_count", "viewCount")),
                                      usability_rating=cls._optional_decimal(
                                          cls._get(dataset, "usability_rating", "usabilityRating")),
                                      remote_version="" if version is None else str(version),
                                      remote_updated_at=cls._optional_datetime(
                                          cls._get(dataset, "last_updated", "lastUpdated")),
                                      metadata=cls._metadata(dataset))

    @staticmethod
    def _single_value(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)

        return getattr(value, name, None)

    @classmethod
    def _get(cls, value: Any, *names: str) -> Any:
        for name in names:
            candidate = cls._single_value(value, name)

            if candidate is not None:
                return candidate

        return None

    @classmethod
    def _first_text(cls, value: Any, *names: str) -> str:
        for name in names:
            text = cls._optional_text(cls._single_value(value, name))

            if text:
                return text

        return ""

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None

        return str(value).strip()

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None

        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _optional_datetime(value: Any) -> datetime | None:
        parsed: datetime

        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            normalized = value.strip()

            if not normalized:
                return None

            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"

            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)

        return parsed

    @staticmethod
    def _optional_boolean(value: Any) -> bool | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"1", "true", "yes", "on"}:
                return True

            if normalized in {"0", "false", "no", "off"}:
                return False

        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)

        return None

    @classmethod
    def _optional_license_names(cls, value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None

        if isinstance(value, (str, bytes)):
            candidates = (value,)
        else:
            try:
                candidates = tuple(value)
            except TypeError:
                return ()

        names: list[str] = []
        seen: set[str] = set()

        for candidate in candidates:
            if isinstance(candidate, Mapping):
                name = cls._optional_text(candidate.get("name"))
            else:
                name = cls._optional_text(candidate)

            if name and name not in seen:
                names.append(name)
                seen.add(name)

        return tuple(names)

    @staticmethod
    def _metadata(dataset: Any) -> dict[str, Any]:
        if isinstance(dataset, Mapping):
            raw = dict(dataset)
        else:
            to_dict = getattr(dataset, "to_dict", None)

            if not callable(to_dict):
                return {}

            try:
                raw = to_dict()
            except Exception:
                return {}

        if not isinstance(raw, dict):
            return {}

        # Ensure the result can be stored in JSONField.
        return json.loads(json.dumps(raw, default=str))
