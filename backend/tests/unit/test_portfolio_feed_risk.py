from __future__ import annotations

from datetime import UTC, datetime

from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.graph import EntityRef, FactEvent, NodeKind
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"


async def test_feed_renders_risk_opened_fact_descriptively() -> None:
    store = InMemoryGraphStore()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="risk",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={
                "risk_key": "feature_no_pr:work_item:wi-1",
                "rule_id": "feature_no_pr",
                "severity": "amber",
                "entity_kind": "work_item",
                "entity_id": "wi-1",
                "workstream_id": "ws-1",
                "project_id": "proj-1",
                "reason": "Feature slice has been active for 5 day(s) with no linked pull request.",
                "evidence_identifier": "wi-1",
                "evidence_url": None,
                "evidence_url_is_user_supplied": False,
                "age_days": 5,
                "owner_id": "dev-1",
                "detected_at": datetime.now(tz=UTC).isoformat(),
                "transition": "opened",
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="risk:demo:feature_no_pr:work_item:wi-1:opened:2026-07-01",
        )
    )
    service = PortfolioFeedService(store)

    feed = await service.feed(TENANT, sources=("risk",))

    assert len(feed.items) == 1
    item = feed.items[0]
    assert item.kind == "risk_opened"
    assert "Risk opened" in item.summary
    assert item.details["rule_id"] == "feature_no_pr"
    assert item.details["severity"] == "amber"
    assert item.details["transition"] == "opened"


async def test_feed_renders_risk_cleared_fact_descriptively() -> None:
    store = InMemoryGraphStore()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="risk",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.WORK_ITEM, id="wi-1"),
            payload={
                "risk_key": "feature_no_pr:work_item:wi-1",
                "rule_id": "feature_no_pr",
                "severity": "amber",
                "entity_kind": "work_item",
                "entity_id": "wi-1",
                "workstream_id": "ws-1",
                "project_id": "proj-1",
                "reason": "Feature slice has been active for 5 day(s) with no linked pull request.",
                "evidence_identifier": "wi-1",
                "evidence_url": None,
                "evidence_url_is_user_supplied": False,
                "age_days": 5,
                "owner_id": None,
                "detected_at": datetime.now(tz=UTC).isoformat(),
                "transition": "cleared",
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="risk:demo:feature_no_pr:work_item:wi-1:cleared:2026-07-02",
        )
    )
    service = PortfolioFeedService(store)

    feed = await service.feed(TENANT, sources=("risk",))

    assert feed.items[0].kind == "risk_cleared"
    assert "Risk cleared" in feed.items[0].summary


async def test_feed_names_people_in_a_cross_person_request() -> None:
    """The feed should read like a sentence about people, not about ids.

    The check-in summary had already been fixed to prefer a recorded name; this
    sibling path had not, so the portfolio feed rendered "U1007 needs U1003 for
    Confirm the refund rounding rules" next to "Check-in updated for Kai
    Thompson".
    """
    store = InMemoryGraphStore()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="cross_person_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="U1003"),
            payload={
                "request_id": "xreq-2",
                "reporter_id": "U1007",
                "reporter_name": "Kai Thompson",
                "referenced_person_id": "U1003",
                "referenced_person_name": "Mina Patel",
                "dependency_kind": "needs_input",
                "dependency_status": "open",
                "transition": "opened",
                "summary": "Confirm the refund rounding rules.",
                "needs_resolution": False,
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="cross-person:xreq-2:opened",
        )
    )
    service = PortfolioFeedService(store)

    feed = await service.feed(TENANT, sources=("cross_person_request",))

    assert feed.items[0].summary == (
        "Cross-person input opened: Kai Thompson needs Mina Patel for "
        "Confirm the refund rounding rules."
    )


async def test_feed_renders_cross_person_request_fact_descriptively() -> None:
    store = InMemoryGraphStore()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="cross_person_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="U-alice"),
            payload={
                "request_id": "xreq-1",
                "reporter_id": "dev-1",
                "referenced_person_id": "U-alice",
                "dependency_kind": "needs_review",
                "dependency_status": "open",
                "transition": "opened",
                "summary": "API schema review",
                "needs_resolution": False,
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="cross-person:xreq-1:opened",
        )
    )
    service = PortfolioFeedService(store)

    feed = await service.feed(TENANT, sources=("cross_person_request",))

    assert len(feed.items) == 1
    item = feed.items[0]
    assert item.kind == "cross_person_request_opened"
    assert item.summary == ("Cross-person review opened: dev-1 needs U-alice for API schema review")
    assert item.details == {
        "request_id": "xreq-1",
        "reporter_id": "dev-1",
        "referenced_person_id": "U-alice",
        "dependency_kind": "needs_review",
        "dependency_status": "open",
        "transition": "opened",
        "summary": "API schema review",
        "needs_resolution": False,
    }
