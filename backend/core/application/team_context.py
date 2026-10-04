"""When a coordinator's check-in reply is confirmed: it gives team context (N16).

A scrum master, product owner or exec with none of their own issues under way
has no ETA to give, so their check-in could only end ``partial`` ("ETA was not
provided"): in R2 Ira's kept two pods, two projects and the program amber. For
such a person no ETA is ever due or asked for, and a reply that gives team
context counts as confirmed:

- it mentions the team's work, issues or progress: an issue key or claim, a
  blocker, a team, pod, stand-up or sprint, or a progress phrase such as "on
  track";
- or it says plainly that nothing is blocking.

A bare "no update" gives no team context. The evaluator reads it as carrying no
status (N22), and even read as status, "no update from the team" names a team
but says nothing about its work. Silence never reaches this module: a person who
does not answer is closed by the non-response path, which never records
confirmed.

Who coordinates is decided by the member's app roles together with the tracker
(the collector checks that no issue of theirs is under way), never by the role
alone: a scrum master with an issue in progress still owes its ETA (837f0818).
"""

from __future__ import annotations

import re
from dataclasses import replace

from core.domain.status import CheckInSignals

# App roles whose members coordinate a team's work rather than own its tickets.
TEAM_CONTEXT_ROLES = frozenset({"sm", "po", "exec"})

_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]+-\d+(?![A-Za-z0-9])")
_NOTHING_BLOCKING = re.compile(
    r"\b(?:no(?:thing)?\s+(?:new\s+|major\s+|real\s+|open\s+|other\s+)?"
    r"(?:blockers?|blocking|impediments?)"
    r"|nothing(?:'s|\s+is)?\s+(?:blocked|blocking)"
    r"|not\s+blocked|unblocked|all\s+clear)\b",
    re.IGNORECASE,
)
_PROGRESS = re.compile(
    r"\b(?:on track|on schedule|progress(?:ing|ed)?|moving (?:along|ahead|forward)|steady|"
    r"ramping up|ahead of schedule|behind schedule|delayed|slipp(?:ing|ed)|in review|merged|"
    r"shipped|released|completed|wrapped up)\b",
    re.IGNORECASE,
)
_TEAM_WORK = re.compile(
    r"\b(?:teams?|pods?|squads?|stand-?ups?|sprints?|backlog|roadmap|releases?|milestones?|"
    r"grooming|refinement|retros?|retrospectives?)\b",
    re.IGNORECASE,
)
_NO_UPDATE = re.compile(
    r"\b(?:no(?:thing)?\s+(?:new\s+)?(?:updates?|news|changes?)|nothing\s+(?:new|to report)|"
    r"same as (?:yesterday|before|last time))\b",
    re.IGNORECASE,
)


def coordinates_team(roles: frozenset[str]) -> bool:
    """Whether a member with these app roles coordinates a team's work (N16)."""
    return bool(roles & TEAM_CONTEXT_ROLES)


def gives_team_context(signals: CheckInSignals | None, text: str) -> bool:
    """Whether a coordinator's reply (its ``signals`` and raw ``text``) gives team context.

    ``signals`` are the check-in's merged signals and ``text`` its messages, so a
    team summary in the first message still counts after a short follow-up.
    """
    if signals is not None and (
        signals.issue_updates or signals.blockers or signals.blockers_answered
    ):
        return True
    if _ISSUE_KEY.search(text) or _NOTHING_BLOCKING.search(text) or _PROGRESS.search(text):
        return True
    return bool(_TEAM_WORK.search(text)) and not _NO_UPDATE.search(text)


def with_team_context(signals: CheckInSignals, *, team_context: bool) -> CheckInSignals:
    """A coordinator's signals as recorded: no ETA is due, and team context settles the rest.

    The ETA counts as answered, as for a team summary about others' issues
    (N33), so it is neither asked for nor reported missing. With team context
    the blocker status counts as answered too, so the reply records confirmed;
    without it a blockers question is still due.
    """
    return replace(
        signals,
        eta_answered=True,
        blockers_answered=signals.blockers_answered or team_context,
    )
