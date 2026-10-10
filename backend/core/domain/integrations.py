from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from core.domain.graph import JsonScalar


@dataclass(frozen=True, kw_only=True)
class UserRef:
    tenant_id: str
    external_id: str
    display_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class SyncCursor:
    value: str | None = None
    updated_at: datetime | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SyncCursorRecord:
    """A stored cursor with the connector and scope it belongs to."""

    connector: str
    scope: str
    cursor: SyncCursor


@dataclass(frozen=True, kw_only=True)
class Project:
    tenant_id: str
    id: str
    key: str
    name: str
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Sprint:
    tenant_id: str
    id: str
    board_id: str
    name: str
    state: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


class IssueState(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True, kw_only=True)
class Issue:
    tenant_id: str
    key: str
    title: str
    state: IssueState
    assignee: UserRef | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class IssueComment:
    id: str
    author: UserRef | None
    created_at: datetime | None
    #: Plain text: rich text flattened, mentions written as "@Name".
    body: str
    mentions: tuple[UserRef, ...] = ()


@dataclass(frozen=True, kw_only=True)
class IssueText:
    """An issue's description and comments, read on demand and never stored."""

    tenant_id: str
    key: str
    state: IssueState
    description: str
    comments: tuple[IssueComment, ...] = ()
    updated_at: datetime | None = None


class PullRequestEventKind(StrEnum):
    """What happened on a pull or merge request, in provider-neutral words."""

    #: A commit on the request, dated by when its author wrote it.
    COMMIT = "commit"
    #: Marked as a draft (GitLab "draft", GitHub "convert to draft").
    DRAFT = "draft"
    #: Marked ready for review.
    READY = "ready"
    #: A person's note or line comment.
    COMMENT = "comment"
    #: A submitted review that neither approved nor was withdrawn (GitHub).
    REVIEW = "review"
    APPROVAL = "approval"
    #: An approval taken back.
    UNAPPROVAL = "unapproval"


@dataclass(frozen=True, kw_only=True)
class PullRequestEvent:
    kind: PullRequestEventKind
    at: datetime
    #: The provider login of who did it; None for a commit.
    actor: str | None = None


@dataclass(frozen=True, kw_only=True)
class PullRequest:
    tenant_id: str
    id: str
    title: str
    author: UserRef
    merged: bool
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
    updated_at: datetime | None = None
    opened_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    labels: tuple[str, ...] = ()
    #: Commits, draft and ready marks, review notes and approvals, oldest first.
    #: None when the provider's history of the request was not read: a request
    #: listed for an author, or one whose history read failed.
    events: tuple[PullRequestEvent, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class Repo:
    tenant_id: str
    id: str
    name: str
    default_branch: str | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Commit:
    tenant_id: str
    repo: str
    sha: str
    message: str
    author: UserRef | None
    committed_at: datetime
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class BuildResult:
    tenant_id: str
    id: str
    status: str
    completed_at: datetime | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class CalendarEvent:
    tenant_id: str
    user: UserRef
    starts_on: date
    ends_on: date
    kind: str
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
