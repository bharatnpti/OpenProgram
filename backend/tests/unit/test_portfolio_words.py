"""The Portfolio's words and order (core/domain/portfolio.py): work items counted, nobody named."""

from __future__ import annotations

from datetime import date, timedelta

from core.domain.forecast import Verdict
from core.domain.portfolio import (
    AgainstPlan,
    HealthCounts,
    PortfolioSort,
    ReasonInputs,
    health_words,
    reason_line,
    sort_key,
    verdict_counts,
)

DAY = date(2026, 6, 15)


def _inputs(verdict: Verdict | None, **changes: object) -> ReasonInputs:
    values: dict[str, object] = {
        "verdict": verdict,
        "target": DAY + timedelta(days=21),
        "target_source": "committed",
        "p50": DAY + timedelta(days=28),
        "p85": DAY + timedelta(days=42),
        "team_date": DAY + timedelta(days=30),
        "team_key": "ATL-2",
        "sample_days": 12,
        "min_sample_days": 10,
        "plan": None,
        "as_of": DAY,
    }
    values.update(changes)
    return ReasonInputs(**values)  # type: ignore[arg-type]


def _plan(added_days: float | None) -> AgainstPlan:
    return AgainstPlan(
        planned_on=DAY - timedelta(days=20),
        planned=8,
        added=1,
        without_label=0,
        change_label="CR",
        added_days=added_days,
    )


def test_each_verdict_says_why_in_one_line() -> None:
    assert reason_line(_inputs(None)).startswith("No forecast is kept for it yet")
    assert reason_line(_inputs(Verdict.DONE)) == "Done: every requirement is in production."
    assert reason_line(_inputs(Verdict.NOT_ENOUGH_DATA, sample_days=4)) == (
        "Not enough history to forecast yet: 4 of 10 working days."
    )
    assert reason_line(_inputs(Verdict.NO_DATE, target=None)) == (
        "No delivery date is committed yet; likely by 27 Jul (85%)."
    )
    assert reason_line(_inputs(Verdict.ON_TRACK, p85=DAY + timedelta(days=14))) == (
        "On track: 85% likely by 29 Jun, 5 working days before the committed date 6 Jul."
    )
    assert reason_line(_inputs(Verdict.AT_RISK)) == (
        "At risk: 50% likely by 13 Jul, but 85% only by 27 Jul, after the committed date 6 Jul."
    )
    assert reason_line(_inputs(Verdict.OFF_TRACK)) == (
        "Off track: even the 50% date, 13 Jul, is 5 working days after the committed date 6 Jul."
    )
    # Without a forecast, the team's own date says it.
    assert reason_line(_inputs(Verdict.OFF_TRACK, p50=None, p85=None)) == (
        "Off track: the team's own date 15 Jul (ATL-2) is after the committed date 6 Jul."
    )
    # Jira's release date stands in for a committed one, and a later year is said.
    assert "Jira's release date 6 Jul" in reason_line(
        _inputs(Verdict.AT_RISK, target_source="jira_release")
    )
    assert "2027" in reason_line(_inputs(Verdict.AT_RISK, p85=date(2027, 1, 8)))


def test_scope_added_leads_only_when_it_moved_the_date() -> None:
    assert reason_line(_inputs(Verdict.OFF_TRACK, plan=_plan(4.0))) == (
        "Off track because 1 requirement was added after planning (26 May), "
        "pushing it back 4 working days."
    )
    # Additions the kept days say moved nothing are not the cause.
    assert reason_line(_inputs(Verdict.OFF_TRACK, plan=_plan(0.2))).startswith(
        "Off track: even the 50% date"
    )
    assert reason_line(_inputs(Verdict.OFF_TRACK, plan=_plan(None))).startswith("Off track: even")


def test_health_is_counted_in_words() -> None:
    assert health_words(HealthCounts(reported=False)) == "no status reported yet"
    assert health_words(HealthCounts(reported=True)) == "no open blockers"
    assert health_words(HealthCounts(reported=True, asked=4)) == "no open blockers, all replied"
    assert (
        health_words(
            HealthCounts(
                reported=True, blockers=2, blocked_tasks=1, disagree=1, asked=8, unanswered=3
            )
        )
        == "2 open blockers, 1 blocked task, signals disagree on 1 issue, 3 of 8 not replied"
    )
    assert health_words(HealthCounts(reported=False, asked=2, unanswered=2)) == (
        "no open blockers, 2 of 2 not replied"
    )


def test_one_order_worst_first_never_by_person() -> None:
    rows = [
        ("Done", Verdict.DONE, 0, None, None),
        ("Fine", Verdict.ON_TRACK, 5, -3, DAY),
        ("Late soon", Verdict.OFF_TRACK, 5, 4, DAY + timedelta(days=3)),
        ("Late long", Verdict.OFF_TRACK, 5, 9, DAY + timedelta(days=9)),
        ("Undated", Verdict.NO_DATE, 5, None, None),
        ("Empty", Verdict.AT_RISK, 0, None, DAY),
        ("Unkept", None, 1, None, None),
    ]

    def ordered(sort: PortfolioSort) -> list[str]:
        return [
            name
            for name, *_ in sorted(
                rows,
                key=lambda row: sort_key(
                    sort=sort,
                    verdict=row[1],
                    total=row[2],
                    days_late=row[3],
                    target=row[4],
                    name=row[0],
                ),
            )
        ]

    assert ordered(PortfolioSort.WORST) == [
        "Late long",
        "Late soon",
        "Undated",
        "Unkept",
        "Empty",
        "Fine",
        "Done",
    ]
    assert ordered(PortfolioSort.NAME)[0] == "Done"
    # The same committed date: the worse one first.
    assert ordered(PortfolioSort.COMMITTED)[:2] == ["Empty", "Fine"]
    assert ordered(PortfolioSort.DAYS_LATE)[:2] == ["Late long", "Late soon"]
    counts = verdict_counts(row[1] for row in rows)
    assert counts["off_track"] == 2 and counts["none"] == 1 and sum(counts.values()) == 7
