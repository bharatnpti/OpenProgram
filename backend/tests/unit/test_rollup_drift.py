"""Open drift on a person's issues turns their cell amber, and rolls up (N3).

R1-R4: drift signals were only signals. CHK-17's merge request merged while
Jira kept it In Progress, its owner read green, and so did Platform, Checkout
and the program: the "says green, signals disagree" case OpenProgram exists to
show never reached the heat map. Now an open drift signal on an issue makes its
assignee amber with a "Signals disagree: ..." reason, in the pods a blocker on
that issue would count in, and on up. Never red on drift alone, never on a
silent person, and gone at the next rollup once it clears.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import review_without_merge_request_fact
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.application.rollup_service import RollupService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.risk import RiskProviderConfig
from core.domain.rollup import FactorKind, NodeStatus, Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import _factors_from_json, _factors_to_json

TENANT = "demo"
DAY = date(2026, 10, 5)
OMAR, ASHA, NOAH = "U-omar", "U-asha", "U-noah"
CHK17_DRIFT = "Signals disagree: CHK-17 merged but still open in Jira."
_JIRA = "https://jira.example.test"

_NODES: tuple[GraphNode, ...] = (
    Program(tenant_id=TENANT, id="program-platform", name="Digital Platform Program"),
    Project(
        tenant_id=TENANT,
        id="project-checkout",
        name="Checkout Revamp",
        metadata={"github_repos": "acme/platform-libs,acme/checkout-api"},
    ),
    Project(
        tenant_id=TENANT,
        id="project-identity",
        name="Identity Platform",
        metadata={"github_repos": "acme/identity-service"},
    ),
    Pod(tenant_id=TENANT, id="pod-platform", name="Platform Pod"),
    Pod(tenant_id=TENANT, id="pod-payments", name="Payments Pod"),
    Pod(tenant_id=TENANT, id="pod-identity", name="Identity Pod"),
    Developer(tenant_id=TENANT, id=OMAR, name="Omar Haddad"),
    Developer(tenant_id=TENANT, id=ASHA, name="Asha Rao"),
    Developer(tenant_id=TENANT, id=NOAH, name="Noah Weber"),
    Task(
        tenant_id=TENANT,
        id="CHK-17",
        name="Upgrade shared HTTP client",
        metadata={"key": "CHK-17", "state": "in_progress", "status": "In Progress"},
    ),
    Task(
        tenant_id=TENANT,
        id="CHK-6",
        name="Refund edge cases",
        metadata={"key": "CHK-6", "state": "in_progress", "status": "In Progress"},
    ),
)
_CONTAINS = (
    ("program-platform", "project-checkout"),
    ("program-platform", "project-identity"),
    ("project-checkout", "pod-platform"),
    ("project-checkout", "pod-payments"),
    ("project-identity", "pod-identity"),
    ("pod-platform", OMAR),
    ("pod-platform", ASHA),
    ("pod-identity", OMAR),
    ("pod-identity", NOAH),
    ("pod-payments", NOAH),
    ("pod-platform", "CHK-17"),
    ("project-checkout", "CHK-17"),
    ("pod-payments", "CHK-6"),
    ("project-checkout", "CHK-6"),
)
_ASSIGNED = ((OMAR, "CHK-17"), (NOAH, "CHK-6"))


async def _org(*, omar: StatusSource | None = StatusSource.CONFIRMED) -> InMemoryGraphStore:
    """Omar (Platform, Identity) owns CHK-17; Noah (Payments, Identity) owns CHK-6."""
    store = InMemoryGraphStore()
    for node in _NODES:
        await store.upsert_node(node)
    for parent, child in _CONTAINS:
        await store.add_edge(_edge(parent, child, EdgeKind.CONTAINS))
    for developer, task in _ASSIGNED:
        await store.add_edge(_edge(developer, task, EdgeKind.ASSIGNED_TO))
    for developer in (ASHA, NOAH):
        await store.record_developer_status(_status(developer, StatusSource.CONFIRMED))
    if omar is not None:
        await store.record_developer_status(_status(OMAR, omar))
    return store


def _edge(parent: str, child: str, kind: EdgeKind) -> GraphEdge:
    return GraphEdge(tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=kind)


def _status(developer_id: str, source: StatusSource) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id=TENANT,
        developer_id=developer_id,
        as_of=DAY,
        source=source,
        blockers=(),
        summary="On track, no blockers.",
        developer_confirmed=source is StatusSource.CONFIRMED,
    )


async def _merge_request(
    store: InMemoryGraphStore,
    *,
    repo: str,
    number: str,
    branch: str,
    days_ago: int,
    merged: bool,
    author: str,
    opened_days_ago: int | None = None,
) -> None:
    observed = datetime.combine(DAY - timedelta(days=days_ago), datetime.min.time(), UTC)
    opened = DAY - timedelta(days=opened_days_ago if opened_days_ago is not None else days_ago)
    await store.append_fact(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=author),
            payload={
                "repo": repo,
                "id": number,
                "title": branch,
                "source_branch": branch,
                "merged": merged,
                "state": "merged" if merged else "open",
                "draft": False,
                "opened_at": datetime.combine(opened, datetime.min.time(), UTC).isoformat(),
                "web_url": f"https://git.example.test/{repo}/-/merge_requests/{number}",
            },
            observed_at=observed,
            correlation_id=f"pr-{repo}-{number}-{days_ago}-{merged}",
        )
    )


async def _chk17_merged(store: InMemoryGraphStore, *, days_ago: int = 1) -> None:
    await _merge_request(
        store,
        repo="acme/platform-libs",
        number="1",
        branch="CHK-17-upgrade-shared-http-client",
        days_ago=days_ago,
        merged=True,
        author=ASHA,
        opened_days_ago=3,
    )


def _risk(store: InMemoryGraphStore, **config: object) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(jira_base_url=_JIRA, **config),  # type: ignore[arg-type]
    )


async def _rollup(store: InMemoryGraphStore, **config: object) -> dict[str, NodeStatus]:
    service = RollupService(
        store, store, BlockerResolutionService(store, store), drift_signals=_risk(store, **config)
    )
    tree = await store.get_program_tree(TENANT, "program-platform", DAY)
    return {status.entity_ref.id: status for status in await service.compute(tree, DAY)}


def _drift(status: NodeStatus) -> list[str]:
    return [factor.description for factor in status.factors if factor.kind is FactorKind.DRIFT]


_TEAMS = ("pod-platform", "pod-payments", "pod-identity", "project-checkout", "project-identity")


async def test_a_watermelon_turns_its_owner_and_teams_amber_then_green_once_it_clears() -> None:
    # Omar says he is on track; CHK-17 merged a day ago and Jira still has it open.
    store = await _org()
    await _chk17_merged(store)

    by_id = await _rollup(store)

    omar = by_id[OMAR]
    assert omar.rag is Rag.AMBER
    assert omar.source is StatusSource.CONFIRMED  # what he said is kept, not rewritten
    assert [(factor.kind, factor.description) for factor in omar.factors] == [
        (FactorKind.DRIFT, CHK17_DRIFT)
    ]
    drift = omar.factors[0]
    assert drift.contributes is Rag.AMBER
    assert drift.work_item_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-17")
    assert drift.applies_to_pod_ids == ("pod-platform",)
    for node_id in ("pod-platform", "project-checkout", "program-platform"):
        assert by_id[node_id].rag is Rag.AMBER, node_id
        assert _drift(by_id[node_id]) == [CHK17_DRIFT], node_id
    # CHK-17 is Platform's: Omar's other pod, and its project, stay green.
    assert by_id["pod-identity"].rag is Rag.GREEN
    assert by_id["project-identity"].rag is Rag.GREEN
    stored = await store.latest_developer_status(TENANT, OMAR, DAY)
    assert stored is not None and stored.source is StatusSource.CONFIRMED

    # Jira catches up: the next rollup has nothing to say about CHK-17.
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-17",
            name="Upgrade shared HTTP client",
            metadata={"key": "CHK-17", "state": "done", "status": "Done"},
        )
    )

    cleared = await _rollup(store)

    assert cleared[OMAR].rag is Rag.GREEN
    assert {cleared[node].rag for node in (*_TEAMS, "program-platform")} == {Rag.GREEN}
    assert not any(_drift(status) for status in cleared.values())


async def test_merged_issue_open_inside_its_grace_day_is_not_amber() -> None:
    store = await _org()
    await _chk17_merged(store, days_ago=0)  # merged today: the issue sync may not have run

    by_id = await _rollup(store)

    assert by_id[OMAR].rag is Rag.GREEN
    assert by_id["pod-platform"].rag is Rag.GREEN
    assert by_id["program-platform"].rag is Rag.GREEN


async def test_drift_on_a_silent_owner_stays_unknown() -> None:
    # No status from Omar at all, then an unknown one: silence is never green,
    # and a drift does not make it amber either.
    for omar in (None, StatusSource.UNKNOWN):
        store = await _org(omar=omar)
        await _chk17_merged(store)

        by_id = await _rollup(store)

        assert by_id[OMAR].rag is Rag.UNKNOWN, omar
        assert _drift(by_id[OMAR]) == [], omar
        assert by_id["pod-platform"].rag is Rag.UNKNOWN, omar
        assert not any(_drift(status) for status in by_id.values()), omar


async def test_drift_beside_partial_or_inferred_silence_keeps_amber_with_both_reasons() -> None:
    store = await _org(omar=StatusSource.INFERRED)
    await _chk17_merged(store)

    omar = (await _rollup(store))[OMAR]

    assert omar.rag is Rag.AMBER
    assert [factor.kind for factor in omar.factors] == [FactorKind.STATUS, FactorKind.DRIFT]


async def test_drift_alone_is_never_red_and_blockers_still_are() -> None:
    store = await _org()
    await _chk17_merged(store)
    # A second drift on Omar: CHK-18 merged and still open as well.
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-18",
            name="Shared config loader",
            metadata={"key": "CHK-18", "state": "in_progress", "status": "In Progress"},
        )
    )
    for parent, child in (("pod-platform", "CHK-18"), ("project-checkout", "CHK-18")):
        await store.add_edge(_edge(parent, child, EdgeKind.CONTAINS))
    await store.add_edge(_edge(OMAR, "CHK-18", EdgeKind.ASSIGNED_TO))
    await _merge_request(
        store,
        repo="acme/platform-libs",
        number="2",
        branch="CHK-18-shared-config-loader",
        days_ago=2,
        merged=True,
        author=OMAR,
    )

    by_id = await _rollup(store)

    assert len(_drift(by_id[OMAR])) == 2
    assert by_id[OMAR].rag is Rag.AMBER
    assert by_id["pod-platform"].rag is Rag.AMBER
    assert by_id["program-platform"].rag is Rag.AMBER

    # Two open blockers make him red, as they always did; the drift stays beside them.
    await store.record_developer_status_with_blockers(
        _status(OMAR, StatusSource.CONFIRMED),
        tuple(
            DeveloperBlocker(
                tenant_id=TENANT,
                blocker_id=f"blk-{index}",
                developer_id=OMAR,
                description=text,
                normalized_key=normalize_blocker_key(text),
                work_item_id="CHK-17",
                source=BlockerSource.CHECKIN,
                first_seen_on=DAY,
                last_seen_on=DAY,
            )
            for index, text in enumerate(("Waiting on a security review.", "Staging is down."))
        ),
    )

    blocked = await _rollup(store)

    assert blocked[OMAR].rag is Rag.RED
    assert len(_drift(blocked[OMAR])) == 2
    assert blocked["pod-platform"].rag is Rag.RED


async def test_drift_on_an_issue_nobody_is_assigned_turns_nobody_amber() -> None:
    store = await _org()
    await store.remove_edge(_edge(OMAR, "CHK-17", EdgeKind.ASSIGNED_TO))
    await _chk17_merged(store)

    by_id = await _rollup(store)

    assert {status.rag for status in by_id.values() if status.entity_ref.id in (OMAR, ASHA)} == {
        Rag.GREEN
    }
    assert by_id["program-platform"].rag is Rag.GREEN


async def test_an_ageing_merge_request_counts_until_it_merges() -> None:
    # Noah says CHK-6 is on track; checkout-api !3 has been open 3 days (threshold 2).
    store = await _org()
    await _merge_request(
        store,
        repo="acme/checkout-api",
        number="3",
        branch="CHK-6-refund-edge-cases",
        days_ago=3,
        merged=False,
        author=NOAH,
    )
    risk = _risk(store, default_pr_age_days=2)
    await risk.assess_and_persist_project(TENANT, "project-checkout", DAY)

    by_id = await _rollup(store, default_pr_age_days=2)

    assert by_id[NOAH].rag is Rag.AMBER
    # The finding keeps ageing from the day it was assessed (wall clock), so
    # the count depends on when this runs; the wording does not.
    (reason,) = _drift(by_id[NOAH])
    assert re.fullmatch(
        r"Signals disagree: CHK-6 has a merge request open \d+ days \(checkout-api !3\)\.",
        reason,
    )
    assert by_id["pod-payments"].rag is Rag.AMBER
    assert by_id["pod-identity"].rag is Rag.GREEN  # CHK-6 is a Payments issue
    assert [finding.rule_id.value for finding in await risk.portfolio_risks(TENANT, DAY)] == [
        "pr_age"
    ]

    # It merges at noon: the next rollup, and the Signals list, stop counting it
    # without waiting for the evening assessment.
    await _merge_request(
        store,
        repo="acme/checkout-api",
        number="3",
        branch="CHK-6-refund-edge-cases",
        days_ago=0,
        merged=True,
        author=NOAH,
        opened_days_ago=3,
    )

    merged = await _rollup(store, default_pr_age_days=2)

    assert merged[NOAH].rag is Rag.GREEN
    assert merged["pod-payments"].rag is Rag.GREEN
    assert await risk.portfolio_risks(TENANT, DAY) == []


async def test_an_ageing_merge_request_with_a_disclosed_blocker_is_no_drift() -> None:
    store = await _org()
    await _merge_request(
        store,
        repo="acme/checkout-api",
        number="3",
        branch="CHK-6-refund-edge-cases",
        days_ago=3,
        merged=False,
        author=NOAH,
    )
    text = "CHK-6 waits on a review from Liam."
    await store.record_developer_status_with_blockers(
        _status(NOAH, StatusSource.CONFIRMED),
        (
            DeveloperBlocker(
                tenant_id=TENANT,
                blocker_id="blk-chk6",
                developer_id=NOAH,
                description=text,
                normalized_key=normalize_blocker_key(text),
                work_item_id="CHK-6",
                source=BlockerSource.CHECKIN,
                first_seen_on=DAY,
                last_seen_on=DAY,
            ),
        ),
    )
    await _risk(store, default_pr_age_days=2).assess_and_persist_project(
        TENANT, "project-checkout", DAY
    )

    noah = (await _rollup(store, default_pr_age_days=2))[NOAH]

    assert noah.rag is Rag.AMBER  # the blocker, said by him
    assert _drift(noah) == []


async def test_the_heat_map_names_the_drift_on_the_owner_and_up_the_tree() -> None:
    store = await _org()
    await _chk17_merged(store)
    for status in (await _rollup(store)).values():
        await store.record_node_status(status)
    views = PersonaViewService(store, store, store, store)

    heatmap = await views.portfolio_heatmap(TENANT, DAY)

    why = {cell.entity_ref.id: cell.why for cell in heatmap.cells}
    assert why[OMAR] == CHK17_DRIFT
    expected = "Signals disagree: CHK-17 merged but still open in Jira (Omar Haddad)."
    for node_id in ("pod-platform", "project-checkout", "program-platform"):
        assert why[node_id] == expected, node_id


async def test_a_drift_factor_survives_the_stored_rollup_row() -> None:
    store = await _org()
    await _chk17_merged(store)
    omar = (await _rollup(store))[OMAR]

    assert _factors_from_json(_factors_to_json(omar.factors)) == omar.factors


async def _said_in_review(store: InMemoryGraphStore, issue_key: str, speaker: str) -> None:
    await store.append_fact(
        review_without_merge_request_fact(
            tenant_id=TENANT,
            issue_key=issue_key,
            developer_id=speaker,
            developer_name=speaker,
            as_of=DAY,
            status_source=StatusSource.CONFIRMED,
            observed_at=datetime.combine(DAY, datetime.min.time(), UTC),
            correlation_id=f"corr-{speaker}-{issue_key}",
        )
    )


async def test_in_review_without_a_merge_request_counts_for_code_work_only() -> None:
    store = await _org()
    # Mina, a product owner, reviews CHK-10's acceptance criteria: no code (N26).
    await store.upsert_node(
        Developer(tenant_id=TENANT, id="U-mina", name="Mina Patel", metadata={"app_roles": "po"})
    )
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-10",
            name="Checkout acceptance criteria",
            metadata={"key": "CHK-10", "state": "in_progress", "status": "In Progress"},
        )
    )
    for parent, child in (
        ("pod-payments", "U-mina"),
        ("pod-payments", "CHK-10"),
        ("project-checkout", "CHK-10"),
    ):
        await store.add_edge(_edge(parent, child, EdgeKind.CONTAINS))
    await store.add_edge(_edge("U-mina", "CHK-10", EdgeKind.ASSIGNED_TO))
    await store.record_developer_status(_status("U-mina", StatusSource.CONFIRMED))
    await _said_in_review(store, "CHK-10", "U-mina")
    # Noah, a developer, says his CHK-6 is in review, and no merge request names it.
    await _said_in_review(store, "CHK-6", NOAH)

    by_id = await _rollup(store)

    assert by_id["U-mina"].rag is Rag.GREEN
    assert by_id[NOAH].rag is Rag.AMBER
    assert _drift(by_id[NOAH]) == [
        "Signals disagree: CHK-6 said to be in review, but no open merge request names it."
    ]
    assert by_id["pod-payments"].rag is Rag.AMBER

    # The merge request is opened and synced: the next rollup drops it.
    await _merge_request(
        store,
        repo="acme/checkout-api",
        number="3",
        branch="CHK-6-refund-edge-cases",
        days_ago=0,
        merged=False,
        author=NOAH,
    )

    opened = await _rollup(store)

    assert opened[NOAH].rag is Rag.GREEN
    assert opened["pod-payments"].rag is Rag.GREEN
