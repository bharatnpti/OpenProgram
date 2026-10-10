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


class IssueCreateFailed(ProviderUnavailable):
    """The issue tracker did not create an issue: ``category`` says why, in a fixed word.

    ``fields`` names the fields a refusal was about (the tracker's field ids,
    never its messages), so the reason can be told without provider text.
    """

    def __init__(self, category: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(f"issue tracker did not create the issue: {category}")
        self.category = category
        self.fields = fields


class SecretNotFound(OpenProgramError):
    """Raised when a requested connector secret does not exist."""


class GraphNotFound(OpenProgramError):
    """Raised when a graph entity cannot be found."""
