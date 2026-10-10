"""The day report's facts for the console's pictures, and the sent report they leave alone.

The console draws Daily from ``DayReport.facts``. Two things are pinned here:
what is sent (the text Send now and the schedule send, and every channel's
format) is byte for byte what it was before the facts existed, and each fact
says no more than the report's own lines.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.day_report_builder import _reason_kind
from core.domain.delivery import DeliveryStage
from core.domain.escalation_matrix import NeedType
from core.domain.forecast import Verdict
from core.domain.graph import Developer
from core.domain.report_facts import BypassFacts, StageCount, StageMoveFacts
from core.domain.reports import render_text
from tests.fixtures.day_report_full_day import DAYS, NOTE, formats, full_report
from tests.unit.test_day_reports import TENANT, _groups, _report_body, _section

S = DeliveryStage
GOLDEN = Path(__file__).parent / "golden"


# --- What is sent does not change --------------------------------------------------------


async def test_what_is_sent_is_byte_for_byte_what_it_was_before_the_facts() -> None:
    """The golden files were written from this same day by the builder as it was
    before the facts existed (41b21b99), with ``tests/fixtures/day_report_full_day.py``.
    Send now and the schedule send the plain text; a person's direct message, an
    email and a Teams card carry the other three."""
    for stem, verdict in DAYS.items():
        report = await full_report(verdict)

        assert report.facts is not None
        for name, rendered in formats(report).items():
            golden = (GOLDEN / f"{stem}.{name}").read_bytes()
            assert rendered.encode() == golden, f"{stem}.{name}"


async def test_no_format_reads_the_facts() -> None:
    for verdict in DAYS.values():
        report = await full_report(verdict)

        assert formats(replace(report, facts=None)) == formats(report)


def test_the_preview_carries_the_facts_beside_the_unchanged_text(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"chat_provider": "fake"}))
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.graph_repository().upsert_node(
                Developer(tenant_id=TENANT, id="U1001", name="Dana")
            )
        )
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        report_id = client.post("/day-reports", json=_report_body("checkout")).json()["report_id"]
        preview = client.get(f"/day-reports/{report_id}/preview").json()
        sent = client.post(f"/day-reports/{report_id}/send").json()

    facts = preview["facts"]
    assert set(facts) == {"note", "delivery", "progress", "gates", "important", "asks"}
    assert facts["progress"]["stages"] == []
    assert facts["asks"] == []
    assert preview["text"].startswith(preview["title"] + "\n")
    # A send records the report's text and nothing of the facts.
    assert "facts" not in sent
    assert sent["title"] == preview["title"]


# --- Each fact says no more than its line ------------------------------------------------


async def test_the_date_facts_name_only_what_the_report_says() -> None:
    report = await full_report(Verdict.AT_RISK)
    assert report.facts is not None
    delivery = report.facts.delivery
    assert delivery is not None
    in_short = _section(report, "In short").lines
    important = _section(report, "Most important").lines

    assert delivery.verdict is Verdict.AT_RISK
    assert (delivery.target, delivery.target_source) == (date(2026, 11, 21), "committed")
    assert in_short[1].startswith("Delivery Sat 21 Nov 2026: at risk.")
    # Most important names who committed it, how it moved and how short the history is,
    # so the facts do; the 50% date it never states.
    assert important[0] == (
        "Committed for Sat 21 Nov 2026 by Priya; moved once, 7 days later than first set."
    )
    assert (delivery.committed_by, delivery.times_moved, delivery.moved_days) == ("Priya", 1, 7)
    assert important[1] == "Only 1 working day of history; a forecast needs 10."
    assert (delivery.history_days, delivery.history_needed) == (1, 10)
    assert delivery.no_forecast_reason == important[1]
    assert (delivery.p50, delivery.p85) == (None, None)
    assert report.facts.important.drawn == ("committed", "history")
    # The third reason is not on the bar, so it stays a line.
    assert report.facts.important.lines == (important[2],)
    assert report.facts.important.risks == 0


@pytest.mark.parametrize("verdict", [Verdict.ON_TRACK, Verdict.NOT_ENOUGH_DATA])
async def test_a_date_that_is_not_in_danger_names_nobody(verdict: Verdict) -> None:
    """While the date is not in danger Most important leaves it out, so the facts do too."""
    report = await full_report(verdict, note=False)

    assert report.facts is not None
    delivery = report.facts.delivery
    assert delivery is not None
    assert _section(report, "In short").lines[0].startswith("Delivery Sat 21 Nov 2026: ")
    assert not any(line.startswith("• Committed for") for line in render_text(report).splitlines())
    assert delivery.verdict is verdict
    assert (delivery.committed_by, delivery.times_moved, delivery.moved_days) == (None, 0, None)
    assert (delivery.history_days, delivery.no_forecast_reason, delivery.p50) == (None, None, None)
    assert report.facts.important.drawn == ()
    assert report.facts.important.lines == ()
    assert report.facts.note is None


async def test_the_stage_facts_are_the_lines_under_where_we_stand() -> None:
    report = await full_report()
    assert report.facts is not None
    progress = report.facts.progress
    stand = _groups(_section(report, "Where we stand"))

    assert (progress.since, progress.total) == (date(2026, 10, 2), 3)
    assert progress.percent == report.percent_complete
    assert progress.stages == (
        StageCount(stage=S.RAISED, count=0, previous=0),
        StageCount(stage=S.GROOMED, count=0, previous=0),
        StageCount(stage=S.IN_DEVELOPMENT, count=1, previous=2),
        StageCount(stage=S.IN_TESTING, count=1, previous=0),
        StageCount(stage=S.BUSINESS_TESTING, count=0, previous=0),
        StageCount(stage=S.PRODUCTION, count=1, previous=0),
    )
    assert stand["Progress"][1] == (
        "Raised 0 · Groomed 0 · In development 1 (-1) · In testing 1 (+1) · "
        "Business testing 0 · Production 1 (+1)"
    )
    assert progress.moves == (
        StageMoveFacts(
            key="CHK-1", title="CHK-1 work", from_stage=S.IN_DEVELOPMENT, to_stage=S.IN_TESTING
        ),
        StageMoveFacts(
            key="CHK-2", title="CHK-2 work", from_stage=S.IN_DEVELOPMENT, to_stage=S.PRODUCTION
        ),
        StageMoveFacts(key="CHK-3", title="CHK-3 work", from_stage=None, to_stage=S.IN_DEVELOPMENT),
    )
    assert progress.more_moves == 0
    changed = stand["What changed since Fri 2 Oct 2026"]
    # The moves are the strip's; what else changed stays in the report's words.
    assert progress.other_changes == changed[3:]
    assert progress.other_changes[0] == "Scope +1 requirement"
    assert progress.notes == ()


async def test_the_gate_facts_count_each_requirement_once() -> None:
    report = await full_report()
    assert report.facts is not None
    acceptance, engineering = report.facts.gates
    lines = _groups(_section(report, "Where we stand"))["Acceptance and tests"]

    assert lines[0].startswith("Business acceptance (before production): 0 of 3 passed")
    assert lines[0].endswith("; 1 moved on without it.")
    assert (acceptance.name, acceptance.guards_stage, acceptance.total) == (
        "Business acceptance",
        S.PRODUCTION,
        3,
    )
    parts = (
        acceptance.passed,
        acceptance.bypassed,
        acceptance.failed,
        acceptance.open,
        acceptance.missing,
    )
    assert sum(parts) == acceptance.total
    assert (acceptance.passed, acceptance.bypassed) == (0, 1)
    assert (engineering.name, engineering.bypassed) == ("Engineering delivery", 1)
    assert report.facts.important.bypassed == (
        BypassFacts(
            key="CHK-2",
            stage=S.PRODUCTION,
            gates=("Business acceptance", "Engineering delivery"),
        ),
    )


async def test_the_ask_facts_are_the_groups_under_what_we_need() -> None:
    report = await full_report()
    assert report.facts is not None
    needs = _section(report, "What we need, and from whom")

    assert [owner.heading for owner in report.facts.asks] == [g.heading for g in needs.groups]
    for owner, group in zip(report.facts.asks, needs.groups, strict=True):
        assert len(owner.asks) == len(group.lines)
        for ask, line in zip(owner.asks, group.lines, strict=True):
            assert line.startswith(f"{ask.need.value.capitalize()}: {ask.text}")
            assert ("Escalated to" in line) == (ask.escalated_to is not None)
            if ask.escalated_to:
                assert line.endswith(f"Escalated to {ask.escalated_to} ({ask.escalation_label}).")
    most = [ask for owner in report.facts.asks for ask in owner.asks if ask.needed_most]
    needed_line = _section(report, "In short").lines[2]
    assert needed_line.startswith("Needed most: ")
    assert len(most) == 3
    for ask in most:
        assert ask.issue_key is None or f"on {ask.issue_key}" in needed_line
    question = next(ask for owner in report.facts.asks for ask in owner.asks if ask.open_question)
    assert (question.need, question.issue_key) == (NeedType.ANSWER, "CHK-1")
    assert question.detail == "asked by Asha (partly answered)"
    assert report.facts.note is not None
    assert (report.facts.note.author, report.facts.note.text) == ("Priya", NOTE.text)
    assert _section(report, "In short").lines[0] == f"Priya: {NOTE.text}"


def test_the_reason_openings_are_the_forecasts_own_sentences() -> None:
    assert _reason_kind("Committed for Sat 21 Nov 2026 by Priya.") == "committed"
    assert _reason_kind("No delivery date is committed yet.") == "no_date"
    assert _reason_kind("No date committed; Jira's release date Fri 30 Oct 2026 is used.") == "jira"
    assert (
        _reason_kind("History: 50% likely by Wed 2 Dec 2026, 85% by Mon 21 Dec 2026 (13 to go).")
        == "forecast"
    )
    assert _reason_kind("Only 3 working days of history; a forecast needs 10.") == "history"
    assert (
        _reason_kind("No history yet: a forecast needs 10 working days of daily snapshots.")
        == "history"
    )
    assert (
        _reason_kind("Team dates: the latest open requirement is due Fri 9 Oct 2026 (CHK-4).")
        == "team"
    )
    # Kept as lines: nothing on the bar draws them.
    assert _reason_kind("2 open requirements have no ETA or due date.") is None
    assert _reason_kind("Only Pod committed Fri 4 Dec 2026, after the project's 1 Dec.") is None
    assert (
        _reason_kind("History and the team's dates are 12 working days apart; one is wrong.")
        is None
    )
