from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.person_names import PersonNames
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.directory import DirectoryUser
from core.domain.graph import Developer, EntityRef, FactEvent, JsonScalar, NodeKind
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.registry import ServiceRegistry

TENANT = "demo"

# Made-up chat ids: one for a member, one known only to the chat directory, and
# one nothing names.
MEMBER_ID = "U0123ABCD"
DIRECTORY_ONLY_ID = "U0456EFGH"
UNKNOWN_ID = "U0789IJKL"
RAW_IDS = (MEMBER_ID, DIRECTORY_ONLY_ID, UNKNOWN_ID)


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
    # Nothing names either person here, so neither reads as their id.
    assert item.summary == (
        "Cross-person review opened: a team member needs a team member for API schema review"
    )
    assert item.person_name == "a team member"
    assert item.details == {
        "request_id": "xreq-1",
        "reporter_id": "dev-1",
        "reporter_name": "a team member",
        "referenced_person_id": "U-alice",
        "referenced_person_name": "a team member",
        "dependency_kind": "needs_review",
        "dependency_status": "open",
        "transition": "opened",
        "summary": "API schema review",
        "needs_resolution": False,
    }


async def _seed_people_and_nameless_facts(store: InMemoryGraphStore) -> None:
    """A member, a directory-only person, an unknown id, and facts naming none of them."""
    await store.upsert_node(Developer(tenant_id=TENANT, id=MEMBER_ID, name="Rosa Lind"))
    await store.upsert_users(
        [
            # The member's own record wins over the directory's name for them.
            DirectoryUser(tenant_id=TENANT, external_id=MEMBER_ID, display_name="Rosa L."),
            DirectoryUser(
                tenant_id=TENANT, external_id=DIRECTORY_ONLY_ID, display_name="Kai Thompson"
            ),
        ]
    )
    now = datetime.now(tz=UTC)

    def fact(
        source: str,
        person_id: str,
        payload: dict[str, JsonScalar],
        minutes_ago: int,
        kind: NodeKind = NodeKind.DEVELOPER,
    ) -> FactEvent:
        return FactEvent(
            tenant_id=TENANT,
            source=source,
            entity_ref=EntityRef(tenant_id=TENANT, kind=kind, id=person_id),
            payload=payload,
            observed_at=now - timedelta(minutes=minutes_ago),
            correlation_id=f"{source}:{person_id}:{minutes_ago}",
        )

    for event in (
        # An older check-in fact recorded no name at all.
        fact("checkin", MEMBER_ID, {"status_source": "confirmed", "blocker_count": 0}, 1),
        # One whose recorded "name" is only the id.
        fact(
            "checkin",
            UNKNOWN_ID,
            {"developer_name": UNKNOWN_ID, "status_source": "partial", "blocker_count": 1},
            2,
        ),
        fact(
            "cross_person_request",
            MEMBER_ID,
            {
                "request_id": "xreq-3",
                "reporter_id": DIRECTORY_ONLY_ID,
                "referenced_person_id": MEMBER_ID,
                "dependency_kind": "needs_review",
                "dependency_status": "open",
                "transition": "opened",
                "summary": "review checkout-api !3",
                "needs_resolution": False,
            },
            3,
        ),
        fact(
            "vcs_pull_request",
            DIRECTORY_ONLY_ID,
            {"repo": "acme/checkout-api", "id": "3", "title": "Refund edge cases"},
            4,
        ),
        fact(
            "issue",
            "CHK-6",
            {"key": "CHK-6", "state": "in_progress", "title": "Refund edge cases"},
            5,
            kind=NodeKind.TASK,
        ),
    ):
        await store.append_fact_once(event)


async def test_feed_names_every_person_never_a_raw_chat_id() -> None:
    """N37: the Signals feed showed people as raw chat ids.

    Each card labelled a person entity with its id, and a fact recorded without
    a name -- or with only the id -- put the id in the summary. Every person now
    reads as the member's name, else the directory's, else "a team member".
    """
    store = InMemoryGraphStore()
    await _seed_people_and_nameless_facts(store)
    service = PortfolioFeedService(
        store,
        person_names=PersonNames(
            graph_repository=store,
            directory_repository=InMemoryDirectoryUserRepository(store),
        ),
    )

    feed = await service.feed(TENANT)

    by_source = {(item.source, item.entity_ref.id): item for item in feed.items}
    member_checkin = by_source[("checkin", MEMBER_ID)]
    assert member_checkin.person_name == "Rosa Lind"
    assert member_checkin.summary == "Check-in updated for Rosa Lind: confirmed, 0 blocker(s)"
    unknown_checkin = by_source[("checkin", UNKNOWN_ID)]
    assert unknown_checkin.person_name == "a team member"
    assert unknown_checkin.summary == "Check-in updated for a team member: partial, 1 blocker(s)"
    request = by_source[("cross_person_request", MEMBER_ID)]
    assert request.person_name == "Rosa Lind"
    assert request.summary == (
        "Cross-person review opened: Kai Thompson needs Rosa Lind for review checkout-api !3"
    )
    assert request.details["reporter_name"] == "Kai Thompson"
    assert request.details["referenced_person_name"] == "Rosa Lind"
    assert by_source[("vcs_pull_request", DIRECTORY_ONLY_ID)].person_name == "Kai Thompson"
    # An issue is not a person; its key already reads as a name.
    assert by_source[("issue", "CHK-6")].person_name is None
    for item in feed.items:
        shown = (item.summary, item.person_name or "")
        assert not any(raw in text for raw in RAW_IDS for text in shown), shown


def test_feed_api_carries_person_names(settings: Settings) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_people_and_nameless_facts(store))
    mgr_settings = settings.model_copy(update={"dev_principal_roles": "mgr"})
    app = create_app(
        settings=mgr_settings, registry=ServiceRegistry(mgr_settings, graph_store=store)
    )

    with TestClient(app) as client:
        response = client.get("/portfolio/feed")

    assert response.status_code == 200
    items = response.json()["items"]
    named = {(item["source"], item["entity_ref"]["id"]): item["person_name"] for item in items}
    assert named == {
        ("checkin", MEMBER_ID): "Rosa Lind",
        ("checkin", UNKNOWN_ID): "a team member",
        ("cross_person_request", MEMBER_ID): "Rosa Lind",
        ("vcs_pull_request", DIRECTORY_ONLY_ID): "Kai Thompson",
        ("issue", "CHK-6"): None,
    }
    summaries = " ".join(item["summary"] for item in items)
    assert "Kai Thompson needs Rosa Lind" in summaries
    assert not any(raw in summaries for raw in RAW_IDS)


def _risk_fact(rule_id: str, entity_kind: NodeKind, entity_id: str) -> FactEvent:
    now = datetime.now(tz=UTC)
    risk_key = f"{rule_id}:{entity_kind.value}:{entity_id}"
    return FactEvent(
        tenant_id=TENANT,
        source="risk",
        entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=MEMBER_ID),
        payload={
            "risk_key": risk_key,
            "rule_id": rule_id,
            "severity": "amber",
            "entity_kind": entity_kind.value,
            "entity_id": entity_id,
            "workstream_id": None,
            "project_id": "proj-1",
            "reason": "Pull request 'Refund edge cases' in acme/checkout-api is 4 day(s) old.",
            "evidence_identifier": "acme/checkout-api#3",
            "evidence_url": None,
            "evidence_url_is_user_supplied": False,
            "age_days": 4,
            "threshold_days": 3,
            "owner_id": MEMBER_ID,
            "detected_at": now.isoformat(),
            "transition": "opened",
        },
        observed_at=now,
        correlation_id=f"risk:{TENANT}:{risk_key}:opened",
    )


def test_portfolio_risks_name_a_person_entity(settings: Settings) -> None:
    """A merge request no work item claims is filed on its author: name them."""
    store = InMemoryGraphStore()
    asyncio.run(_seed_people_and_nameless_facts(store))
    asyncio.run(store.append_fact_once(_risk_fact("pr_age", NodeKind.DEVELOPER, MEMBER_ID)))
    asyncio.run(store.append_fact_once(_risk_fact("feature_no_pr", NodeKind.WORK_ITEM, "wi-1")))
    mgr_settings = settings.model_copy(update={"dev_principal_roles": "mgr"})
    app = create_app(
        settings=mgr_settings, registry=ServiceRegistry(mgr_settings, graph_store=store)
    )

    with TestClient(app) as client:
        response = client.get("/portfolio/risks")

    assert response.status_code == 200
    named = {risk["entity_ref"]["id"]: risk["person_name"] for risk in response.json()["risks"]}
    assert named == {MEMBER_ID: "Rosa Lind", "wi-1": None}


async def test_feed_details_say_how_a_brief_may_read_eta_changes_and_merge_requests() -> None:
    """A check-in's ETA change carries whether the N45 parser read it; a merge
    request its address, which says whether its host writes "!1" or "#1"."""
    store = InMemoryGraphStore()
    now = datetime.now(tz=UTC)
    person = EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-1")
    for fact in (
        FactEvent(
            tenant_id=TENANT,
            source="checkin",
            entity_ref=person,
            payload={
                "status_source": "confirmed",
                "blocker_count": 0,
                "eta_change_days": 2,
                "eta_change_checked": True,
            },
            observed_at=now,
            correlation_id="checkin-new",
        ),
        FactEvent(
            tenant_id=TENANT,
            source="checkin",
            entity_ref=person,
            # Recorded before the marker: its number may be a duration.
            payload={"status_source": "confirmed", "blocker_count": 0, "eta_change_days": 2},
            observed_at=now - timedelta(days=1),
            correlation_id="checkin-old",
        ),
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=person,
            payload={
                "repo": "acme/web",
                "id": "4",
                "merged": False,
                "title": "SHOP-11 Cart breakdown",
                "web_url": "https://git.example.test/acme/web/-/merge_requests/4",
            },
            observed_at=now - timedelta(hours=1),
            correlation_id="pr-4",
        ),
    ):
        await store.append_fact(fact)

    feed = await PortfolioFeedService(store).feed(TENANT, sources=("checkin", "vcs_pull_request"))

    new, request, old = feed.items
    assert new.details["eta_change_checked"] is True
    assert old.details["eta_change_checked"] is None
    assert request.details["web_url"] == "https://git.example.test/acme/web/-/merge_requests/4"
