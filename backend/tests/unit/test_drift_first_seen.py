"""A drift finding's age counts from when the scheduled scan first saw it open.

Before, drift was worked out on every read with ``detected_at`` now, so every
drift ask waited "0 days". The scan now records one ``drift_seen`` fact when a
finding first opens and one when it clears; reads date the finding from it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from core.application import risk_service as risk_module
from core.application.blocker_resolution import BlockerResolutionService
from core.application.delivery_scope import DeliveryScopeService
from core.application.portfolio_index import PortfolioIndexReader
from core.application.portfolio_warnings_service import PortfolioWarningsService, WarningKind
from core.application.risk_service import DRIFT_SEEN_FACT_SOURCE, RiskService
from core.domain.auth import Principal, Role
from core.domain.graph import (
    Developer,
    EdgeKind,
    GraphEdge,
    Pod,
    Project,
    WorkItem,
    Workstream,
)
from core.domain.risk import DriftFindingKind
from infra.persistence.in_memory_graph import InMemoryGraphStore

T = "demo"
FIRST = date(2026, 6, 10)
TODAY = date(2026, 6, 15)


class _Clock(datetime):
    """risk_service's clock, set per scan."""

    current = datetime(2026, 6, 10, 18, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object = None) -> _Clock:  # type: ignore[override]
        return cls.fromtimestamp(cls.current.timestamp(), tz=UTC)


def _at(day: date) -> None:
    _Clock.current = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=18)


def _risks(store: InMemoryGraphStore) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
    )


async def _seed(store: InMemoryGraphStore) -> None:
    for node in (
        Project(tenant_id=T, id="proj-1", name="Atlas"),
        Workstream(tenant_id=T, id="ws-1", name="Checkout flow"),
        Pod(tenant_id=T, id="pod-1", name="Atlas Core"),
        Developer(tenant_id=T, id="dev-ada", name="Ada Quill"),
        WorkItem(
            tenant_id=T,
            id="wi-1",
            name="Pay by link",
            metadata={"state": "done", "owner_id": "dev-ada", "key": "ATL-9"},
        ),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("proj-1", "ws-1"),
        ("ws-1", "wi-1"),
        ("proj-1", "pod-1"),
        ("pod-1", "dev-ada"),
    ):
        await store.add_edge(
            GraphEdge(tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS)
        )


async def _set_item(store: InMemoryGraphStore, **metadata: str) -> None:
    await store.upsert_node(
        WorkItem(
            tenant_id=T,
            id="wi-1",
            name="Pay by link",
            metadata={"state": "done", "owner_id": "dev-ada", "key": "ATL-9", **metadata},
        )
    )


def test_a_drift_finding_is_dated_from_the_scan_that_first_saw_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(risk_module, "datetime", _Clock)
    store = InMemoryGraphStore()
    service = _risks(store)

    async def run() -> None:
        await _seed(store)
        _at(FIRST)
        await service.scan_and_record_drift(T, "proj-1", FIRST)
        _at(TODAY)
        (finding,) = await service.project_drift(T, "proj-1", TODAY)
        assert finding.kind is DriftFindingKind.SAID_DONE_NO_PR
        assert finding.detected_at.date() == FIRST
        assert await service.drift_seen_since(T, TODAY) == FIRST
        # Seen again by a later scan, it keeps the day it was first seen.
        await service.scan_and_record_drift(T, "proj-1", TODAY)
        seen = await store.list_recent_facts(T, sources=(DRIFT_SEEN_FACT_SOURCE,), limit=50)
        assert [fact.payload["transition"] for fact in seen] == ["opened"]
        (portfolio,) = await service.portfolio_drift(T, TODAY)
        assert portfolio.detected_at.date() == FIRST

        # It clears: the next scan records that, and a read no longer dates it back.
        await _set_item(store, pr_id="42")
        await service.scan_and_record_drift(T, "proj-1", TODAY)
        assert await service.project_drift(T, "proj-1", TODAY) == []
        # Back the next day: first seen again, that day.
        later = TODAY + timedelta(days=1)
        _at(later)
        await _set_item(store)
        await service.scan_and_record_drift(T, "proj-1", later)
        (again,) = await service.project_drift(T, "proj-1", later)
        assert again.detected_at.date() == later
        # A past day is read as it stood: first seen on FIRST.
        (then,) = await service.project_drift(T, "proj-1", TODAY - timedelta(days=1))
        assert then.detected_at.date() == FIRST

    asyncio.run(run())


def test_early_warnings_say_seen_since_and_name_the_owner_only_to_themself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(risk_module, "datetime", _Clock)
    store = InMemoryGraphStore()
    risks = _risks(store)
    scope = DeliveryScopeService(store, store, today=lambda: TODAY)
    warnings = PortfolioWarningsService(
        index=PortfolioIndexReader(store, scope),
        risks=risks,
        blockers=BlockerResolutionService(store, store),
        graph=store,
        scope=scope,
        tracker="Jira",
    )

    def principal(subject: str, *roles: Role) -> Principal:
        return Principal(tenant_id=T, subject=subject, roles=frozenset(roles))

    async def run() -> None:
        await _seed(store)
        _at(FIRST)
        await risks.scan_and_record_drift(T, "proj-1", FIRST)
        _at(TODAY)
        manager = await warnings.read(principal("dev-mgr", Role.MGR), TODAY)
        owner = await warnings.read(principal("dev-ada", Role.SM), TODAY)
        executive = await warnings.read(principal("dev-exec", Role.EXEC), TODAY)

        assert manager.seen_since == FIRST
        group = next(g for g in manager.groups if g.kind is WarningKind.TOOLS_DISAGREE)
        (item,) = group.items
        assert item.key == "ATL-9" and item.age_days == 5
        # Its age starts on the scan's first recording, so it is "seen since" then.
        assert item.age_known is False and item.since == FIRST
        assert item.line == "ATL-9 is marked done with no merge request."
        # Worded as the item disagreeing: the team, not the person, to anyone else.
        assert item.owner is None and item.owner_team == "Atlas Core"
        (own,) = next(g for g in owner.groups if g.kind is WarningKind.TOOLS_DISAGREE).items
        assert own.owner == "Ada Quill" and own.owner_is_you
        (counted,) = next(g for g in executive.groups if g.kind is WarningKind.TOOLS_DISAGREE).items
        assert counted.owner is None and counted.owner_team == "Atlas Core"

    asyncio.run(run())
