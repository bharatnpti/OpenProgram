"""How an ask's line in "What we need, and from whom" says how long it has waited.

A pull request's age risk carries a length of time in its own text ("has been
open for 7 days"). The ask's wait beside it is a different number: since the
risk was raised, it has waited on its owner. A bare "(4 days)" read as a second,
unexplained figure, so every kind of ask says "waiting N days".
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.day_report_asks import Ask, AskScope, signal_ask, waited_words
from core.domain.escalation_matrix import NeedType
from core.domain.graph import EntityRef, NodeKind, Project
from core.domain.risk import RiskEvidence, RiskFinding, RiskFindingStatus, RiskRuleId
from core.domain.rollup import Rag

TENANT = "demo"
DAY = date(2026, 10, 9)


def _scope() -> AskScope:
    return AskScope(
        project=Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        tasks={},
        teams={},
        all_teams={},
        members=frozenset({"U0000TEST01"}),
        names={"U0000TEST01": "Noor Test"},
        assignees={},
        task_teams={},
        member_teams={},
    )


def _pr_age(detected_on: date) -> RiskFinding:
    return RiskFinding(
        tenant_id=TENANT,
        rule_id=RiskRuleId.PR_AGE,
        severity=Rag.AMBER,
        entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="U0000TEST01"),
        workstream_id=None,
        reason="Pull request 'CHK-11 Cart price breakdown' in acme/storefront-web "
        "has been open for 7 days.",
        evidence=RiskEvidence(identifier="acme/storefront-web!11"),
        age_days=7,
        threshold_days=3,
        detected_at=datetime(detected_on.year, detected_on.month, detected_on.day, 9, tzinfo=UTC),
        status=RiskFindingStatus.OPEN,
        owner_id="U0000TEST01",
    )


def test_a_pull_request_age_ask_says_how_long_it_is_open_and_how_long_it_waited() -> None:
    ask = signal_ask(_pr_age(date(2026, 10, 5)), _scope(), DAY)

    assert ask.owner == "Noor Test"
    assert ask.waited_days == 4
    assert ask.line() == (
        "Fix: Pull request 'CHK-11 Cart price breakdown' in acme/storefront-web "
        "has been open for 7 days (waiting 4 days)."
    )


def test_every_kind_says_its_wait_the_same_way_before_its_detail() -> None:
    def line(need: NeedType, days: int | None, detail: str = "") -> str:
        ask = Ask(need=need, owner="Noor Test", text="CHK-1: x", waited_days=days, detail=detail)
        return ask.line()

    assert line(NeedType.FIX, 1, "reported by Asha") == (
        "Fix: CHK-1: x (waiting 1 day; reported by Asha)."
    )
    assert line(NeedType.REVIEW, 2) == "Review: CHK-1: x (waiting 2 days)."
    assert line(NeedType.ANSWER, 6, "asked by Asha") == (
        "Answer: CHK-1: x (waiting 6 days; asked by Asha)."
    )
    assert line(NeedType.DECISION, 0) == "Decision: CHK-1: x (waiting since today)."
    # Unknown: no wait is said, and the detail stands alone.
    assert line(NeedType.FIX, None, "reported by Asha") == "Fix: CHK-1: x (reported by Asha)."
    assert waited_words(None) == "waiting since today"


def test_the_opening_names_the_wait_once_with_no_second_number() -> None:
    ask = signal_ask(_pr_age(date(2026, 10, 5)), _scope(), DAY)

    # "In short" says only who and how long; the open time is in the line below.
    assert ask.brief() == "a fix from Noor Test (4 days)"
