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


@dataclass(frozen=True, kw_only=True)
class PullRequest:
    tenant_id: str
    id: str
    title: str
    author: UserRef
    merged: bool
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
