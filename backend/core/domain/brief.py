from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

#: A structured brief is a one-line verdict, then this many short bullets:
#: a director reads it in ten seconds.
MIN_BULLETS = 2
MAX_BULLETS = 4
#: How a stored body marks a bullet: "- ".
BULLET_MARK = "- "
_MAX_READ_BULLETS = 10


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

    @property
    def structure(self) -> BriefStructure | None:
        return brief_structure(self.body)


@dataclass(frozen=True, kw_only=True)
class BriefStructure:
    """A brief as a reader takes it in: a one-line verdict, then a few short bullets."""

    verdict: str
    bullets: tuple[str, ...]


def structured_body(verdict: str, bullets: Sequence[str]) -> str:
    """The stored body of a structured brief: the verdict, then one "- " line per bullet.

    Kept as text in the existing ``body`` column, so a reader that knows only
    ``body`` still shows every word of it, and no schema change is needed.
    """
    return "\n".join([verdict.strip(), *(f"{BULLET_MARK}{bullet.strip()}" for bullet in bullets)])


def brief_structure(body: str) -> BriefStructure | None:
    """The verdict and bullets of a structured body; None for an older free-text brief."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0].startswith(BULLET_MARK.strip()):
        return None
    verdict, *rest = lines
    if not all(line.startswith(BULLET_MARK) for line in rest):
        return None
    bullets = tuple(line[len(BULLET_MARK) :].strip() for line in rest)
    # Generation keeps to MAX_BULLETS; reading takes any stored structured
    # body, an older one with more bullets included, but not a list of lines.
    if not all(bullets) or len(bullets) > _MAX_READ_BULLETS:
        return None
    return BriefStructure(verdict=verdict, bullets=bullets)
