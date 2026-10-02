"""How generated status summaries are worded.

When a developer doesn't answer a check-in, the collector writes an inferred,
stale or unknown status whose summary opens with :data:`NON_RESPONSE_LEAD`.
Confirming such a status from the console writes a confirmed status, and its
summary is built here too: it keeps what the status was based on but drops the
non-response framing, so a confirmed status never says it wasn't confirmed.

Every summary built here is assembled from summaries the system already
holds plus their dates; nothing adds a detail the underlying status didn't
carry.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import StatusRepository

NON_RESPONSE_LEAD = "No confirmed check-in after a nudge."
INFERRED_LEAD = f"{NON_RESPONSE_LEAD} Inferred from "
CONFIRMED_LEAD = "Confirmed the status"
# The placeholder blocker a non-response status carries when nothing is open.
NO_REPLY_BLOCKER = "no confirmed reply"

# How far back a stale status is followed to the status it stands in for.
BASIS_LOOKBACK = timedelta(days=90)

# Statuses that say something about the developer's work: a reply, in full or
# in part, or an inference from delivery signals. Stale and unknown don't.
_BASIS_SOURCES = frozenset({StatusSource.CONFIRMED, StatusSource.PARTIAL, StatusSource.INFERRED})


def day_label(day: date) -> str:
    """``Sep 29``: the date format inferred summaries already use."""
    return f"{day:%b} {day.day}"


def inferred_summary(basis: str) -> str:
    return f"{INFERRED_LEAD}{basis}."


def inferred_basis(summary: str) -> str | None:
    """What an inferred summary says it was inferred from; None for other text."""
    if not summary.startswith(INFERRED_LEAD):
        return None
    basis = summary.removeprefix(INFERRED_LEAD).strip().removesuffix(".").rstrip()
    return basis or None


def reported_text(summary: str) -> str:
    """A confirmed or partial summary without a leading non-response sentence.

    Confirm used to copy the non-response summary into the confirmed status
    verbatim, so rows stored before that was fixed can open with it.
    """
    return summary.removeprefix(NON_RESPONSE_LEAD).strip() or summary


async def basis_status(
    repository: StatusRepository,
    status: DeveloperStatus,
    *,
    lookback: timedelta = BASIS_LOOKBACK,
) -> DeveloperStatus | None:
    """The confirmed, partial or inferred status that ``status`` stands for.

    That is ``status`` itself unless it is stale; a stale status is followed
    back, one earlier day at a time, to the most recent status before it that
    says something, giving up after ``lookback``. None for an unknown status,
    or when no such status is found.
    """
    floor = status.as_of - lookback
    current: DeveloperStatus | None = status
    while current is not None and current.source is StatusSource.STALE and current.as_of > floor:
        current = await repository.latest_developer_status(
            current.tenant_id,
            current.developer_id,
            current.as_of - timedelta(days=1),
        )
    if current is None or current.source not in _BASIS_SOURCES or current.as_of < floor:
        return None
    return current


def confirmed_summary(
    existing: DeveloperStatus,
    basis: DeveloperStatus | None,
    as_of: date,
) -> str:
    """The summary of the status written when the developer confirms ``existing``.

    ``basis`` is :func:`basis_status` of ``existing``. Confirming a confirmed or
    partial status of the same day keeps its words. Otherwise the summary says
    what was confirmed: an earlier day's reported status, with that day's date,
    or what an inferred status was inferred from.
    """
    if existing.as_of == as_of and existing.source in {
        StatusSource.CONFIRMED,
        StatusSource.PARTIAL,
    }:
        return existing.summary
    if basis is None:
        return f"{CONFIRMED_LEAD}, with no details recorded."
    if basis.source is StatusSource.INFERRED:
        when = "" if basis.as_of == as_of else f" on {day_label(basis.as_of)}"
        inferred_from = inferred_basis(basis.summary)
        if inferred_from is None:
            return f"{CONFIRMED_LEAD} inferred{when}: {reported_text(basis.summary)}"
        return f"{CONFIRMED_LEAD} inferred{when} from {inferred_from}."
    if basis.summary.startswith(CONFIRMED_LEAD):
        # A re-confirmation already names what it confirmed and when.
        return basis.summary
    return f"{CONFIRMED_LEAD} from {day_label(basis.as_of)}: {reported_text(basis.summary)}"
