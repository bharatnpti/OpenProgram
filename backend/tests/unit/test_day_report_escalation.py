"""How far a day report's ask goes up the escalation matrix, and through which team.

An ask goes up from where its owner stands on the matrix, or its asker when
that is higher, and never down or sideways: on the QA tenant a review the
product owner asked of the manager went to the scrum master below her (BE-5).
A person on several teams climbs from the team the work belongs to, not from
the first one the graph lists (B-V1-1).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from core.application.day_report_asks import (
    Ask,
    AskScope,
    Team,
    escalated,
    request_ask,
    signal_ask,
    standing,
    team_from,
)
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.escalation_matrix import (
    ContactSource,
    EscalationLevel,
    EscalationMatrix,
    NeedType,
    default_matrix,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Project,
    Task,
)
from core.domain.reports import render_text
from core.domain.risk import RiskEvidence, RiskFinding, RiskFindingStatus, RiskRuleId
from core.domain.rollup import Rag
from infra.registry import ServiceRegistry
from tests.unit.test_day_reports import TENANT, TODAY, _seeded_registry

NAMES = {
    "u-asha": "Asha Rao",
    "u-ira": "Ira Novak",
    "u-mina": "Mina Patel",
    "u-liam": "Liam Chen",
    "u-zoe": "Zoe Almeida",
    "u-lena": "Lena Park",
    "u-elena": "Elena Fischer",
}


def _pod(pod_id: str, name: str, repos: str = "", **contacts: str) -> Pod:
    """A pod with escalation contacts by member id, ``sm="u-ira"`` or ``manager="u-asha"``."""
    metadata: dict[str, str | None] = {"github_repos": repos} if repos else {}
    for role, member in contacts.items():
        metadata[f"escalation_{role}_chat_external_id"] = f"C-{member}"
        metadata[f"escalation_{role}_display_name"] = NAMES[member]
        metadata[f"escalation_{role}_member_id"] = member
    return Pod(tenant_id=TENANT, id=pod_id, name=name, metadata=metadata)


#: The QA tenant's Checkout pods: Ira runs Payments, Asha manages both.
PAY = team_from(
    _pod("pod-payments", "Payments Pod", "acme/checkout-api", sm="u-ira", manager="u-asha"),
    NAMES,
)
STORE = team_from(
    _pod("pod-storefront", "Storefront Pod", "acme/storefront-web", sm="u-lena", manager="u-asha"),
    NAMES,
)
#: A pod whose scrum master is a member with the role, not an escalation contact.
DATA = team_from(_pod("pod-data", "Data Pod"), NAMES, scrum_masters=["u-ira"])


def _scope(
    *teams: Team,
    member_teams: dict[str, tuple[str, ...]] | None = None,
    task_teams: dict[str, str] | None = None,
) -> AskScope:
    tasks = {key: Task(tenant_id=TENANT, id=key, name=key) for key in (task_teams or {})}
    return AskScope(
        project=Project(tenant_id=TENANT, id="project-checkout", name="Checkout Revamp"),
        tasks=tasks,
        teams={team.node.id: team for team in teams},
        all_teams={team.node.id: team for team in (PAY, STORE, DATA, *teams)},
        members=frozenset(NAMES),
        names=NAMES,
        assignees={},
        task_teams=task_teams or {},
        member_teams={member: ids[0] for member, ids in (member_teams or {}).items()},
        member_team_ids=member_teams or {},
    )


def _ask(
    owner: str | None,
    days: int,
    *,
    need: NeedType = NeedType.REVIEW,
    team: Team | None = PAY,
    asked_by: str | None = None,
) -> Ask:
    return Ask(
        need=need, owner=owner, text="CHK-3: x", waited_days=days, team=team, asked_by=asked_by
    )


def _went(ask: Ask, matrix: EscalationMatrix | None = None) -> tuple[str | None, str | None]:
    """Where the ask went: ``(escalated_to, nobody_above)``."""
    out = escalated(ask, matrix or default_matrix(TENANT), _scope(PAY, STORE))
    return out.escalated_to, out.nobody_above


def _with_director(after_days: int = 10) -> EscalationMatrix:
    """The default matrix with a director above the managers."""
    matrix = default_matrix(TENANT)
    director = EscalationLevel(
        label="Director",
        source=ContactSource.MEMBER,
        member_id="u-elena",
        after_days={need: after_days for need in NeedType},
    )
    return replace(matrix, levels=(*matrix.levels, director))


def test_a_managers_ask_never_goes_down_to_the_scrum_master() -> None:
    # Mina's review has waited 8 days on Asha, past the scrum master and the manager levels.
    ask = _ask("Asha Rao", 8, asked_by="Mina Patel")

    assert _went(ask) == (None, "Asha Rao")
    assert escalated(ask, default_matrix(TENANT), _scope(PAY)).line() == (
        "Review: CHK-3: x (waiting 8 days). Nobody above Asha Rao is told."
    )
    # Past only the scrum master's level, it is still not sent down to Ira.
    assert _went(_ask("Asha Rao", 3, asked_by="Mina Patel")) == (None, "Asha Rao")
    # Not yet due: nothing is said.
    assert _went(_ask("Asha Rao", 1, asked_by="Mina Patel")) == (None, None)


def test_a_managers_ask_goes_up_to_whoever_stands_above_once_it_reaches_them() -> None:
    matrix = _with_director()

    # Someone stands above, not yet reached: the report waits and says nothing.
    assert _went(_ask("Asha Rao", 8), matrix) == (None, None)
    assert _went(_ask("Asha Rao", 12), matrix) == ("Elena Fischer", None)
    # The top of the matrix: nobody above the director.
    assert _went(_ask("Elena Fischer", 12), matrix) == (None, "Elena Fischer")


def test_an_ask_from_the_manager_goes_up_from_the_managers_level() -> None:
    """Asha asked Liam for a review: Ira, below her, is never told; nobody above her is."""
    assert _went(_ask("Liam Chen", 3, asked_by="Asha Rao")) == (None, "Asha Rao")
    assert _went(_ask("Liam Chen", 12, asked_by="Asha Rao"), _with_director()) == (
        "Elena Fischer",
        None,
    )


def test_an_ask_never_goes_back_to_the_person_who_asked_for_it() -> None:
    """Ira asked Liam, on her own pod: past her level, to the manager when it gets there."""
    assert _went(_ask("Liam Chen", 3, asked_by="Ira Novak")) == (None, None)
    assert _went(_ask("Liam Chen", 5, asked_by="Ira Novak")) == ("Asha Rao", None)


def test_a_scrum_master_of_another_team_is_beside_not_above() -> None:
    """Lena runs Storefront; her ask on Payments skips Ira, a scrum master like her."""
    assert standing("Lena Park", default_matrix(TENANT), _scope(PAY, STORE)) == 2
    # A scrum master by role, with no contact anywhere, stands there too.
    assert standing("Ira Novak", default_matrix(TENANT), _scope(DATA)) == 2
    assert standing("Liam Chen", default_matrix(TENANT), _scope(PAY)) == 1
    assert _went(_ask("Lena Park", 3, need=NeedType.FIX)) == (None, None)
    assert _went(_ask("Lena Park", 5, need=NeedType.FIX)) == ("Asha Rao", None)


def test_a_developers_ask_still_climbs_from_the_scrum_master() -> None:
    assert _went(_ask("Liam Chen", 2, asked_by="Mina Patel")) == ("Ira Novak", None)
    assert _went(_ask("Liam Chen", 5, asked_by="Mina Patel")) == ("Asha Rao", None)


def test_an_ask_with_nobody_named_on_its_team_says_so_once_due() -> None:
    lonely = team_from(_pod("pod-solo", "Solo Pod"), NAMES)

    assert _went(_ask("Liam Chen", 3, team=lonely)) == (None, "Liam Chen")
    # Nobody named for it either: there is no one to say it about.
    assert _went(_ask(None, 3, team=lonely)) == (None, None)


# ---- the team an ask climbs from ---------------------------------------------------------


def _pr_age(owner: str, repo: str) -> RiskFinding:
    return RiskFinding(
        tenant_id=TENANT,
        rule_id=RiskRuleId.PR_AGE,
        severity=Rag.AMBER,
        entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=owner),
        workstream_id=None,
        reason=f"Pull request 'CHK-12 Cart totals' in {repo} has been open for 6 days.",
        evidence=RiskEvidence(identifier=f"{repo}#2"),
        age_days=6,
        threshold_days=3,
        detected_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        status=RiskFindingStatus.OPEN,
        owner_id=owner,
    )


def test_a_merge_request_risk_climbs_from_the_pod_that_holds_its_repository() -> None:
    """Zoe is on Payments and Storefront: her storefront-web request is Storefront's (B-V1-1)."""
    scope = _scope(PAY, STORE, member_teams={"u-zoe": ("pod-payments", "pod-storefront")})

    ask = signal_ask(_pr_age("u-zoe", "acme/storefront-web"), scope, date(2026, 10, 4))
    api = signal_ask(_pr_age("u-zoe", "acme/checkout-api"), scope, date(2026, 10, 4))

    assert ask.team is STORE
    assert escalated(ask, default_matrix(TENANT), scope).escalated_to == "Lena Park"
    assert api.team is PAY


def test_a_request_climbs_from_the_askees_team_that_holds_its_task() -> None:
    scope = _scope(
        PAY,
        STORE,
        member_teams={"u-zoe": ("pod-payments", "pod-storefront")},
        task_teams={"CHK-12": "pod-storefront"},
    )
    request = CrossPersonRequest(
        tenant_id=TENANT,
        id="req-1",
        requester_id="u-liam",
        requester_chat_ref=None,
        counterpart_id="u-zoe",
        kind=CrossPersonRequestKind.REVIEW,
        note="",
        source_correlation_id="corr-1",
        status=CrossPersonRequestStatus.OPEN,
        created_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        updated_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        task_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-12"),
    )

    ask = request_ask(request, scope, date(2026, 10, 4))

    assert ask is not None
    assert (ask.team, ask.asked_by) == (STORE, "Liam Chen")
    # With no task, of her teams on this project, by name: Payments.
    untied = request_ask(replace(request, task_ref=None), scope, date(2026, 10, 4))
    assert untied is not None and untied.team is PAY


# ---- in the report -----------------------------------------------------------------------


async def _with_a_manager(registry: ServiceRegistry) -> None:
    """Mark, the Payments manager contact, as a member, and Mina, a product owner on no team."""
    graph = registry.graph_repository()
    for member_id, name in (("dev-mark", "Mark"), ("dev-mina", "Mina")):
        await graph.upsert_node(Developer(tenant_id=TENANT, id=member_id, name=name))
    pod = await graph.get_node(TENANT, "pod-pay")
    assert pod is not None
    await graph.upsert_node(
        replace(pod, metadata={**pod.metadata, "escalation_manager_member_id": "dev-mark"})
    )
    await graph.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="pod-pay", to_node_id="dev-mark", kind=EdgeKind.CONTAINS
        )
    )
    for request_id, requester, counterpart, days in (
        ("req-mina", "dev-mina", "dev-mark", 8),
        ("req-mark", "dev-mark", "dev-omar", 3),
    ):
        asked_at = datetime(2026, 10, 5, 9, tzinfo=UTC) - timedelta(days=days)
        await registry.cross_person_request_repository().create(
            CrossPersonRequest(
                tenant_id=TENANT,
                id=request_id,
                requester_id=requester,
                requester_chat_ref=None,
                counterpart_id=counterpart,
                kind=CrossPersonRequestKind.REVIEW,
                note="",
                source_correlation_id=f"corr-{request_id}",
                status=CrossPersonRequestStatus.OPEN,
                created_at=asked_at,
                updated_at=asked_at,
                task_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-2"),
            )
        )


async def test_the_report_tells_nobody_below_the_manager_and_says_so() -> None:
    registry, _store = await _seeded_registry()
    await _with_a_manager(registry)

    report = await registry.day_report_builder().build(TENANT, "checkout", TODAY)

    text = render_text(report)
    # Mina's review of 8 days, on the manager: never to Priya, the scrum master below him.
    assert (
        "Review: CHK-2: Mina asked for a review (waiting 8 days). Nobody above Mark is told."
    ) in text
    # The manager's own request of Omar: not to Omar's scrum master, below the manager.
    assert (
        "Review: CHK-2: Mark asked for a review (waiting 3 days). Nobody above Mark is told."
    ) in text
    # Everyone else's asks are as they were.
    assert "Fix: Zoe waits on CHK-3 (waiting 2 days). Escalated to Priya (Scrum master)." in text
    assert "Review: CHK-1: Asha asked for a review (waiting 2 days). Escalated to Lena" in text
    facts = {
        ask.text: (ask.escalated_to, ask.nobody_above)
        for owner in report.facts.asks
        for ask in owner.asks
        if ask.need is NeedType.REVIEW
    }
    assert facts == {
        "CHK-2: Mina asked for a review": (None, "Mark"),
        "CHK-2: Mark asked for a review": (None, "Mark"),
        "CHK-1: Asha asked for a review": ("Lena", None),
    }
