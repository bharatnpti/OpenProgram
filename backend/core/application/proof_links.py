from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from core.domain.graph import JsonScalar
from core.domain.risk import RiskEvidence

_GITHUB_ENTERPRISE_API_SUFFIX = "/api/v3"


def jira_issue_evidence(
    *,
    key: str,
    jira_base_url: str | None,
    metadata: Mapping[str, JsonScalar] | None = None,
) -> RiskEvidence:
    """Build proof evidence for a Jira issue.

    A user-supplied link (``jira_url`` metadata) always wins. Otherwise the
    link is constructed from the existing ``jira_base_url`` setting -- no new
    provider configuration is introduced.
    """
    supplied = _metadata_url(metadata, "jira_url")
    if supplied is not None:
        return RiskEvidence(identifier=key, url=supplied, url_is_user_supplied=True)
    web_base = _jira_web_base(jira_base_url)
    if web_base is None:
        return RiskEvidence(identifier=key, url=None)
    return RiskEvidence(identifier=key, url=f"{web_base}/browse/{key}")


def pull_request_evidence(
    *,
    repo: str,
    pr_id: str,
    github_base_url: str,
    metadata: Mapping[str, JsonScalar] | None = None,
) -> RiskEvidence:
    """Build proof evidence for a pull/merge request.

    A user-supplied link (``pr_url`` metadata) always wins. Otherwise the
    link is constructed from the existing ``github_base_url`` setting.
    """
    identifier = f"{repo}#{pr_id}"
    supplied = _metadata_url(metadata, "pr_url")
    if supplied is not None:
        return RiskEvidence(identifier=identifier, url=supplied, url_is_user_supplied=True)
    web_base = _github_web_base(github_base_url)
    if web_base is None:
        return RiskEvidence(identifier=identifier, url=None)
    return RiskEvidence(identifier=identifier, url=f"{web_base}/{repo}/pull/{pr_id}")


def _metadata_url(metadata: Mapping[str, JsonScalar] | None, key: str) -> str | None:
    if metadata is None:
        return None
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate
    return None


def _jira_web_base(jira_base_url: str | None) -> str | None:
    """Jira's REST API base and browse (web) base share the same host."""
    if not jira_base_url:
        return None
    parsed = urlparse(jira_base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _github_web_base(github_base_url: str) -> str | None:
    """Map the configured GitHub API base to its browsable web host.

    - ``https://api.github.com`` (GitHub cloud) -> ``https://github.com``
    - ``https://ghe.example.com/api/v3`` (GitHub Enterprise) -> ``https://ghe.example.com``
    """
    parsed = urlparse(github_base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    if parsed.netloc == "api.github.com":
        return f"{parsed.scheme}://github.com"
    path = parsed.path.rstrip("/")
    if path.endswith(_GITHUB_ENTERPRISE_API_SUFFIX):
        return f"{parsed.scheme}://{parsed.netloc}"
    return f"{parsed.scheme}://{parsed.netloc}"
