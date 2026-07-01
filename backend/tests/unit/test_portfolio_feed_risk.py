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
