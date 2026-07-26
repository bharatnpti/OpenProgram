from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class IdentityLink:
    """Provider-neutral mapping between a developer node and their provider ids.

    Keyed by ``(tenant_id, developer_id)`` where ``developer_id`` is the
    canonical graph member id (the chat-provider user id in current
    deployments). The optional provider fields let application code resolve the
    right external id per capability port -- for example querying Jira by
    ``jira_account_id`` instead of the chat-provider id that Jira does not index.
    """

    tenant_id: str
    developer_id: str
    chat_user_id: str | None = None
    jira_account_id: str | None = None
    jira_email: str | None = None
    vcs_username: str | None = None
