from collections.abc import Callable

from .base import DatasetProvider
from .exceptions import ProviderNotRegisteredError

ProviderFactory = Callable[[], DatasetProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    @staticmethod
    def _normalize_slug(slug: str) -> str:
        normalized = slug.strip().lower()

        if not normalized:
            raise ValueError("Provider slug cannot be blank.")

        return normalized

    def register(self, slug: str, factory: ProviderFactory, *, replace: bool = False) -> None:
        normalized = self._normalize_slug(slug)

        if normalized in self._factories and not replace:
            raise ValueError(f"Provider '{normalized}' is already registered.")

        self._factories[normalized] = factory

    def create(self, slug: str) -> DatasetProvider:
        normalized = self._normalize_slug(slug)

        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            raise ProviderNotRegisteredError(f"Provider '{normalized}' is not registered.") from exc

        provider = factory()
        provider_slug = getattr(provider, "slug", None)

        if not isinstance(provider_slug, str):
            raise ValueError(f"Provider factory for '{normalized}' "
                             "returned an invalid adapter.")

        if self._normalize_slug(provider_slug) != normalized:
            raise ValueError("Provider factory slug does not match "
                             f"its registry key: '{provider_slug}' "
                             f"!= '{normalized}'.")

        return provider

    def registered_slugs(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
