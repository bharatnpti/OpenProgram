"""Domain and port-level exceptions."""


class OpenProgramError(Exception):
    """Base error for expected OpenProgram failures."""


class AuthorizationDenied(OpenProgramError):
    """Raised when a principal is not authorized for a capability."""


class AuthenticationRequired(OpenProgramError):
    """Raised when request credentials cannot be authenticated."""


class ProviderUnavailable(OpenProgramError):
    """Raised when an external provider cannot satisfy a request."""


class ProviderConfigurationError(ProviderUnavailable):
    """Raised when external provider credentials or permissions are misconfigured."""


class SecretNotFound(OpenProgramError):
    """Raised when a requested connector secret does not exist."""


class GraphNotFound(OpenProgramError):
    """Raised when a graph entity cannot be found."""
