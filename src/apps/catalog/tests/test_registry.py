from django.test import SimpleTestCase

from apps.catalog.providers.base import DatasetProvider
from apps.catalog.providers.dto import (ProviderDatasetDetails, ProviderSearchPage)
from apps.catalog.providers.exceptions import (ProviderNotRegisteredError)
from apps.catalog.providers.registry import ProviderRegistry


class StubProvider(DatasetProvider):
    slug = "stub"

    def search_summary(self, query: str, *, page: int = 1) -> ProviderSearchPage:
        return ProviderSearchPage(query=query, page=page, items=())

    def fetch_details(self, external_id: str) -> ProviderDatasetDetails:
        return ProviderDatasetDetails(external_id=external_id)


class IncompleteProvider(DatasetProvider):
    slug = "incomplete"

    def search_summary(self, query: str, *, page: int = 1) -> ProviderSearchPage:
        return ProviderSearchPage(query=query, page=page, items=())


class ProviderRegistryTests(SimpleTestCase):
    def test_creates_provider_case_insensitively(self):
        registry = ProviderRegistry()
        registry.register("stub", StubProvider)

        provider = registry.create(" STUB ")

        self.assertIsInstance(provider, StubProvider)

    def test_duplicate_registration_is_rejected(self):
        registry = ProviderRegistry()
        registry.register("stub", StubProvider)

        with self.assertRaises(ValueError):
            registry.register("stub", StubProvider)

    def test_unknown_provider_is_rejected(self):
        registry = ProviderRegistry()

        with self.assertRaises(ProviderNotRegisteredError):
            registry.create("unknown")

    def test_incomplete_provider_cannot_be_created(self):
        registry = ProviderRegistry()
        registry.register("incomplete", IncompleteProvider)

        with self.assertRaises(TypeError):
            registry.create("incomplete")
