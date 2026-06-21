from abc import ABC, abstractmethod
from typing import ClassVar

from .dto import (ProviderDatasetDetails, ProviderSearchPage)


class DatasetProvider(ABC):
    slug: ClassVar[str]

    @abstractmethod
    def search_summary(self, query: str, *, page: int = 1) -> ProviderSearchPage:
        raise NotImplementedError

    @abstractmethod
    def fetch_details(self, external_id: str) -> ProviderDatasetDetails:
        raise NotImplementedError
