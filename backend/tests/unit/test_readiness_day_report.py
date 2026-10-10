"""The day report with release readiness: one Most important line per blocking gap and an ask.

With no criteria, or the agent off, the report is what it always was: the golden
files of the full day (test_day_report_facts.py) stay byte for byte. With a
blocking criterion missing close to its date, the report gains its line and a
decision ask, pinned here in a golden day of its own.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.day_report_full_day import READINESS_DAY, formats, readiness_report

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

    assert all(line in important.lines for line in readiness_lines)
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
    assert all(line in report.facts.important.lines for line in readiness_lines)
