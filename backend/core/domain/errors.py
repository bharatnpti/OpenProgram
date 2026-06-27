"""Domain and port-level exceptions."""


class PulseOpsError(Exception):
    """Base error for expected PulseOps failures."""


class AuthorizationDenied(PulseOpsError):
    """Raised when a principal is not authorized for a capability."""


class ProviderUnavailable(PulseOpsError):
    """Raised when an external provider cannot satisfy a request."""


class ProviderConfigurationError(ProviderUnavailable):
    """Raised when external provider credentials or permissions are misconfigured."""


class SecretNotFound(PulseOpsError):
    """Raised when a requested connector secret does not exist."""


class GraphNotFound(PulseOpsError):
    """Raised when a graph entity cannot be found."""
