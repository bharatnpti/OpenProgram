from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BriefKind(StrEnum):
    """The scheduled narrative brief flavors.

    Each kind targets a different scope: pod-level daily summaries, project-level
    weekly updates, and a single portfolio-wide executive brief.
    """

    DAILY_POD = "daily_pod"
    WEEKLY_PROJECT = "weekly_project"
    EXEC = "exec"


@dataclass(frozen=True, kw_only=True)
class NarrativeBrief:
    """A persisted, descriptive narrative brief.

    The ``body`` is a descriptive rollup of facts/feed/statuses only -- it never
    contains raw check-in or DM/reply content. ``sources`` carries entity/fact
    refs so every claim can be drilled back to its origin.
    """

    tenant_id: str
    kind: BriefKind
    scope_id: str
    title: str
    body: str
    generated_at: datetime
    sources: tuple[str, ...] = ()
