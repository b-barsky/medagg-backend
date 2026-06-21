from .kaggle import KaggleProvider
from .registry import ProviderRegistry


provider_registry = ProviderRegistry()
provider_registry.register(
    KaggleProvider.slug,
    KaggleProvider,
)


__all__ = [
    "ProviderRegistry",
    "provider_registry",
]