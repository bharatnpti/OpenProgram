"""The day report with release readiness: one Most important line per blocking gap and an ask.

With no criteria, or the agent off, the report is what it always was: the golden
files of the full day (test_day_report_facts.py) stay byte for byte. With a
blocking criterion missing close to its date, the report gains its line, listed
first in Most important so its cap never folds it away, and a decision ask,
pinned here in golden days of their own.
"""

from __future__ import annotations

from pathlib import Path

from core.application.day_report_builder import MAX_IMPORTANT_LINES
from tests.fixtures.day_report_full_day import (
    CROWDED_DAY,
    READINESS_DAY,
    formats,
    readiness_report,
)

GOLDEN = Path(__file__).parent / "golden"


async def test_a_blocking_gap_near_its_date_is_said_once_with_its_ask() -> None:
    """Written by ``python -m tests.fixtures.day_report_full_day <dir>`` from this builder."""
    report = await readiness_report()

    for name, rendered in formats(report).items():
        golden = (GOLDEN / f"{READINESS_DAY}.{name}").read_bytes()
        assert rendered.encode() == golden, f"{READINESS_DAY}.{name}"


async def test_the_lines_and_asks_name_scopes_and_work_and_the_facts_follow_the_text() -> None:
    report = await readiness_report()
    important = next(section for section in report.sections if section.title == "Most important")
    asks = next(
        section for section in report.sections if section.title == "What we need, and from whom"
    )
    readiness_lines = (
        "CHK-2 reached production without a security review for Checkout Revamp.",
        "Payments Pod needs an on-call handover within 4 working days, and none is in Jira.",
    )

    # First, ahead of the gate bypass the day also has.
    assert important.lines[:2] == readiness_lines
    decisions = [line for group in asks.groups for line in group.lines if "Decision:" in line]
    assert decisions == [
        "Decision: Checkout Revamp: no security review in Jira yet (due 9 Nov); "
        "create it or link one (since today).",
        "Decision: Payments Pod: no on-call handover in Jira yet (due 9 Oct); "
        "create it or link one (since today).",
    ]
    # The advisory accessibility check is on the board, never in the report.
    assert "accessibility" not in "\n".join(important.lines).casefold()
    assert report.facts is not None
    assert report.facts.important.readiness_gaps == 2
    assert report.facts.important.lines[:2] == readiness_lines


# ---- More than Most important's eight lines --------------------------------------------


async def test_a_crowded_day_lists_the_blocking_gaps_first_and_folds_only_the_rest() -> None:
    """Written by ``python -m tests.fixtures.day_report_full_day <dir>`` from this builder."""
    report = await readiness_report(crowded=True)

    for name, rendered in formats(report).items():
        golden = (GOLDEN / f"{CROWDED_DAY}.{name}").read_bytes()
        assert rendered.encode() == golden, f"{CROWDED_DAY}.{name}"
    important = next(section for section in report.sections if section.title == "Most important")
    gaps = (
        "CHK-4 reached production without an on-call handover for Payments Pod.",
        "CHK-2 reached production without a security review for Checkout Revamp.",
    )
    # Ten lines to say: the two gaps, the date's three reasons and five gate bypasses.
    assert len(important.lines) == MAX_IMPORTANT_LINES + 1
    assert important.lines[:2] == gaps
    assert important.lines[2].startswith("Committed for ")
    assert important.lines[-1] == "and 2 more."
    assert report.facts is not None
    assert report.facts.important.lines[:2] == gaps
