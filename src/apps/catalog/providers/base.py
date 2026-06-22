from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from .dto import (
    ProviderArtifactDownload,
    ProviderDatasetDetails,
    ProviderSearchPage,
)
from .exceptions import ProviderDownloadUnsupportedError


class DatasetProvider(ABC):
    slug: ClassVar[str]

    @abstractmethod
    def search_summary(
        self,
        query: str,
        *,
        page: int = 1,
    ) -> ProviderSearchPage:
        raise NotImplementedError

    @abstractmethod
    def fetch_details(
        self,
        external_id: str,
    ) -> ProviderDatasetDetails:
        raise NotImplementedError

    def download_artifact(
        self,
        external_id: str,
        destination: Path,
    ) -> ProviderArtifactDownload:
        """
        Download the provider's canonical source artifact.

        This method is intentionally optional so a future HTML-only catalog
        adapter can participate in search before it supports downloads.
        """

        raise ProviderDownloadUnsupportedError(
            f"Provider '{self.slug}' does not support artifact downloads."
        )
