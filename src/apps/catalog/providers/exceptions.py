class ProviderError(RuntimeError):
    """Base exception for provider failures."""


class ProviderConfigurationError(ProviderError):
    """The provider is not installed or configured correctly."""


class ProviderAuthenticationError(ProviderError):
    """Provider authentication failed."""


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached or failed operationally."""


class ProviderResponseError(ProviderError):
    """Provider data could not be normalized."""


class ProviderNotRegisteredError(ProviderConfigurationError):
    """No adapter is registered for a requested source."""


class ProviderDownloadUnsupportedError(ProviderConfigurationError):
    """The provider adapter cannot download dataset artifacts."""
