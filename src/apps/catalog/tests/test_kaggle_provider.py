import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from apps.catalog.providers.exceptions import (ProviderResponseError, ProviderUnavailableError)
from apps.catalog.providers.kaggle import KaggleProvider


class FakeKaggleDataset:
    def __init__(self, *, ref: str = "Owner/Lung-Data") -> None:
        self.ref = ref
        self.url = ("https://www.kaggle.com/datasets/"
                    "owner/lung-data")
        self.title = "Lung Data"
        self.owner_name = "Owner"
        self.owner_ref = "owner"
        self.total_bytes = 1024
        self.download_count = 7
        self.vote_count = 2
        self.view_count = 9
        self.usability_rating = 0.875
        self.current_version_number = 3
        self.last_updated = datetime(2026, 1, 1, 12, 0)

    def to_dict(self):
        return {"ref": self.ref, "last_updated": self.last_updated}


class FakeKaggleApi:
    def __init__(self, *, list_results=None, list_error: Exception | None = None, metadata_payload=None,
                 metadata_error: Exception | None = None) -> None:
        self.list_results = list(list_results or ())
        self.list_error = list_error
        self.metadata_payload = metadata_payload
        self.metadata_error = metadata_error

        self.list_calls: list[dict[str, object]] = []
        self.metadata_calls: list[dict[str, object]] = []

    def dataset_list(self, *, search: str, page: int):
        self.list_calls.append({"search": search, "page": page})

        if self.list_error is not None:
            raise self.list_error

        return self.list_results

    def dataset_metadata(self, dataset: str, path: str) -> str:
        self.metadata_calls.append({"dataset": dataset, "path": path})

        if self.metadata_error is not None:
            raise self.metadata_error

        metadata_path = Path(path) / "dataset-metadata.json"
        payload = self.metadata_payload

        if isinstance(payload, str):
            metadata_path.write_text(payload, encoding="utf-8")
        else:
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")

        return str(metadata_path)


class KaggleProviderTests(SimpleTestCase):
    def test_search_normalizes_summary_without_fetching_details(self):
        api = FakeKaggleApi(list_results=[FakeKaggleDataset()])

        result = KaggleProvider(api).search_summary(" lung cancer ", page=2)

        self.assertEqual(api.list_calls, [{"search": "lung cancer", "page": 2}])
        self.assertEqual(api.metadata_calls, [])
        self.assertEqual(result.query, "lung cancer")
        self.assertEqual(result.page, 2)
        self.assertEqual(len(result.items), 1)

        item = result.items[0]

        self.assertEqual(item.external_id, "owner/lung-data")
        self.assertEqual(item.remote_version, "3")
        self.assertEqual(item.total_bytes, 1024)
        self.assertEqual(item.usability_rating, Decimal("0.875"))
        self.assertIsNotNone(item.remote_updated_at)
        self.assertIsNotNone(item.remote_updated_at.tzinfo)
        self.assertEqual(item.metadata["ref"], "Owner/Lung-Data")

    def test_summary_api_failure_is_wrapped(self):
        provider = KaggleProvider(FakeKaggleApi(list_error=RuntimeError("network down")))

        with self.assertRaises(ProviderUnavailableError):
            provider.search_summary("lungs")

    def test_summary_without_reference_is_rejected(self):
        provider = KaggleProvider(FakeKaggleApi(list_results=[FakeKaggleDataset(ref="")]))

        with self.assertRaises(ProviderResponseError):
            provider.search_summary("lungs")

    def test_blank_summary_query_is_rejected(self):
        with self.assertRaises(ValueError):
            KaggleProvider(FakeKaggleApi()).search_summary(" ")

    def test_invalid_summary_page_is_rejected(self):
        with self.assertRaises(ValueError):
            KaggleProvider(FakeKaggleApi()).search_summary("lungs", page=0)

    def test_fetch_detail_normalizes_complete_metadata(self):
        api = FakeKaggleApi(metadata_payload={"info": {"title": "Detailed Lung Data", "subtitle": "Clinical cohort",
                                                       "description": "Complete description",
                                                       "licenses": [{"name": "CC0"}, {"name": "CC BY 4.0"},
                                                                    {"name": "CC0"}], "isPrivate": False,
                                                       "image": "https://example.com/cover.png"},
                                              "resources": [{"path": "patients.csv"}]})

        details = KaggleProvider(api).fetch_details(" Owner/Lung-Data ")

        self.assertEqual(api.metadata_calls[0]["dataset"], "owner/lung-data")
        self.assertEqual(details.external_id, "owner/lung-data")
        self.assertEqual(details.title, "Detailed Lung Data")
        self.assertEqual(details.subtitle, "Clinical cohort")
        self.assertEqual(details.description, "Complete description")
        self.assertEqual(details.license_names, ("CC0", "CC BY 4.0"))
        self.assertFalse(details.is_private)
        self.assertEqual(details.thumbnail_url, "https://example.com/cover.png")
        self.assertEqual(details.metadata["resources"][0]["path"], "patients.csv")

    def test_omitted_detail_fields_remain_none(self):
        provider = KaggleProvider(FakeKaggleApi(metadata_payload={"info": {"title": "Lung Data"}}))

        details = provider.fetch_details("owner/lung-data")

        self.assertEqual(details.title, "Lung Data")
        self.assertIsNone(details.subtitle)
        self.assertIsNone(details.description)
        self.assertIsNone(details.license_names)
        self.assertIsNone(details.is_private)
        self.assertIsNone(details.thumbnail_url)

    def test_explicitly_empty_detail_fields_are_preserved(self):
        provider = KaggleProvider(
            FakeKaggleApi(metadata_payload={"info": {"description": "", "licenses": [], "isPrivate": False}}))

        details = provider.fetch_details("owner/lung-data")

        self.assertEqual(details.description, "")
        self.assertEqual(details.license_names, ())
        self.assertFalse(details.is_private)

    def test_null_primary_thumbnail_uses_available_fallback(self):
        provider = KaggleProvider(FakeKaggleApi(
            metadata_payload={"info": {"image": None, "thumbnailImageUrl": "https://example.com/fallback.png"}}))

        details = provider.fetch_details("owner/lung-data")

        self.assertEqual(details.thumbnail_url, "https://example.com/fallback.png")

    def test_unknown_boolean_value_is_treated_as_omitted(self):
        provider = KaggleProvider(FakeKaggleApi(metadata_payload={"info": {"isPrivate": "unknown"}}))

        details = provider.fetch_details("owner/lung-data")

        self.assertIsNone(details.is_private)

    def test_metadata_api_failure_is_wrapped(self):
        provider = KaggleProvider(FakeKaggleApi(metadata_error=RuntimeError("network down")))

        with self.assertRaises(ProviderUnavailableError):
            provider.fetch_details("owner/lung-data")

    def test_invalid_metadata_json_is_rejected(self):
        provider = KaggleProvider(FakeKaggleApi(metadata_payload="not-json"))

        with self.assertRaises(ProviderResponseError):
            provider.fetch_details("owner/lung-data")

    def test_invalid_metadata_info_is_rejected(self):
        provider = KaggleProvider(FakeKaggleApi(metadata_payload={"info": []}))

        with self.assertRaises(ProviderResponseError):
            provider.fetch_details("owner/lung-data")

    def test_blank_external_id_is_rejected(self):
        with self.assertRaises(ValueError):
            KaggleProvider(FakeKaggleApi()).fetch_details(" ")
