"""The day report with release readiness: one Most important line per blocking gap and an ask.

With no criteria, or the agent off, the report is what it always was: the golden
files of the full day (test_day_report_facts.py) stay byte for byte. With a
blocking criterion missing close to its date, the report gains its line, listed
first in Most important so its cap never folds it away, and a decision ask to
whoever decides on it (``readiness_decider``), pinned here in golden days of
their own.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.application.day_report_asks import (
    Ask,
    AskScope,
    Team,
    escalated,
    readiness_decider,
    team_from,
)
from core.application.day_report_builder import MAX_IMPORTANT_LINES
from core.domain.escalation_matrix import EscalationMatrix, NeedType, default_matrix
from core.domain.graph import Pod, Project
from tests.fixtures.day_report_full_day import (
    CROWDED_DAY,
    READINESS_DAY,
    a_full_day,
    formats,
    readiness_report,
)
from tests.unit.test_day_reports import TENANT

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

    # First, ahead of the gate bypass the day also has.
    assert important.lines[:2] == readiness_lines
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
    assert report.facts.important.lines[:2] == readiness_lines


# ---- More than Most important's eight lines --------------------------------------------


async def test_a_crowded_day_lists_the_blocking_gaps_first_and_folds_only_the_rest() -> None:
    """Written by ``python -m tests.fixtures.day_report_full_day <dir>`` from this builder."""
    report = await readiness_report(crowded=True)

    for name, rendered in formats(report).items():
        golden = (GOLDEN / f"{CROWDED_DAY}.{name}").read_bytes()
        assert rendered.encode() == golden, f"{CROWDED_DAY}.{name}"
    important = next(section for section in report.sections if section.title == "Most important")
    gaps = (
        "CHK-4 reached production without an on-call handover for Payments Pod.",
        "CHK-2 reached production without a security review for Checkout Revamp.",
    )
    # Ten lines to say: the two gaps, the date's three reasons and five gate bypasses.
    assert len(important.lines) == MAX_IMPORTANT_LINES + 1
    assert important.lines[:2] == gaps
    assert important.lines[2].startswith("Committed for ")
    assert important.lines[-1] == "and 2 more."
    assert report.facts is not None
    assert report.facts.important.lines[:2] == gaps


# ---- Who decides on a gap ----------------------------------------------------------------

NAMES = {"u-po": "Mina", "u-po2": "Hana", "u-sm": "Ira", "u-decider": "Dora"}


def _pod(pod_id: str, name: str, **contacts: str) -> Pod:
    """A pod with escalation contacts, ``sm="Priya"`` or ``manager="Mark"``."""
    metadata: dict[str, str | None] = {}
    for role, person in contacts.items():
        metadata[f"escalation_{role}_chat_external_id"] = f"U-{person.upper()}"
        metadata[f"escalation_{role}_display_name"] = person
    return Pod(tenant_id=TENANT, id=pod_id, name=name, metadata=metadata)


PAY = team_from(
    _pod("pod-pay", "Payments Pod", sm="Priya", manager="Mark"),
    NAMES,
    product_owners=["u-po", "u-po2"],
    scrum_masters=["u-sm"],
)
STORE = team_from(_pod("pod-store", "Storefront Pod"), NAMES, product_owners=["u-po2"])
DATA = team_from(_pod("pod-data", "Data Pod", manager="Max"), NAMES, scrum_masters=["u-sm"])
EMPTY = team_from(_pod("pod-empty", "Empty Pod"), NAMES)


def _scope(*teams: Team, owner: str | None = None) -> AskScope:
    return AskScope(
        project=Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        tasks={},
        teams={team.node.id: team for team in teams},
        all_teams={team.node.id: team for team in teams},
        members=frozenset(),
        names=NAMES,
        assignees={},
        task_teams={},
        member_teams={},
        owner=owner,
    )


def _matrix(decision_owner: str | None = None) -> EscalationMatrix:
    return replace(default_matrix(TENANT, "checkout"), decision_owner_id=decision_owner)


def _climbed(owner: str | None, team: Team | None, days: int) -> str | None:
    ask = Ask(need=NeedType.DECISION, owner=owner, text="x", waited_days=days, team=team)
    return escalated(ask, default_matrix(TENANT, "checkout"), NAMES).escalated_to


def test_step_one_the_matrix_decision_owner_then_the_project_owner() -> None:
    by_matrix = readiness_decider(_scope(PAY, owner="Olga"), _matrix("u-decider"))
    by_owner = readiness_decider(_scope(PAY, owner="Olga"), _matrix())
    for_a_pod = readiness_decider(_scope(PAY, owner="Olga"), _matrix(), "pod-pay")

    assert (by_matrix.owner, by_matrix.team) == ("Dora", PAY)
    assert (by_owner.owner, by_owner.team) == ("Olga", PAY)
    # The chain is the same for a pod's gap: the recorded owner comes first.
    assert (for_a_pod.owner, for_a_pod.team) == ("Olga", PAY)


def test_step_two_a_product_owner_of_the_scope_first_pod_by_name_then_by_name() -> None:
    # Payments sorts before Storefront; within Payments, Hana before Mina.
    project = readiness_decider(_scope(STORE, PAY), _matrix())
    pod = readiness_decider(_scope(STORE, PAY), _matrix(), "pod-store")
    without_one = readiness_decider(_scope(DATA, PAY), _matrix(), "pod-data")

    assert PAY.product_owners == ("Hana", "Mina")
    assert (project.owner, project.team) == ("Hana", PAY)
    assert (pod.owner, pod.team) == ("Hana", STORE)
    # A pod's gap asks that pod's product owner, never another pod's.
    assert without_one.owner == "Ira"


def test_step_three_a_pods_scrum_master_contact_then_a_member_with_the_role() -> None:
    contact = team_from(_pod("pod-x", "X Pod", sm="Priya"), NAMES, scrum_masters=["u-sm"])

    by_contact = readiness_decider(_scope(contact), _matrix(), "pod-x")
    by_role = readiness_decider(_scope(DATA), _matrix(), "pod-data")
    project = readiness_decider(_scope(DATA), _matrix())

    assert (by_contact.owner, by_contact.team) == ("Priya", contact)
    assert (by_role.owner, by_role.team) == ("Ira", DATA)
    # A project's gap is never a scrum master's: it goes on to the manager.
    assert project.owner == "Max"


def test_step_four_a_manager_from_the_contacts_who_climbs_no_lower() -> None:
    staffed = team_from(_pod("pod-m", "M Pod", sm="Priya", manager="Mark"), NAMES)
    managed = team_from(_pod("pod-n", "N Pod", manager="Nils"), NAMES)

    project = readiness_decider(_scope(EMPTY, staffed, managed), _matrix())
    pod = readiness_decider(_scope(managed), _matrix(), "pod-n")

    # The project's first pod by name that names a manager; a pod's own for its gap.
    assert (project.owner, project.team) == ("Mark", None)
    assert (pod.owner, pod.team) == ("Nils", None)
    # The ask stays the manager's past both levels: the scrum master below is not named.
    assert _climbed(project.owner, project.team, 9) is None


def test_nobody_named_only_when_the_whole_chain_is_empty() -> None:
    project = readiness_decider(_scope(EMPTY), _matrix())
    pod = readiness_decider(_scope(EMPTY), _matrix(), "pod-empty")
    unknown_pod = readiness_decider(_scope(EMPTY), _matrix(), "pod-gone")

    assert (project.owner, project.team) == (None, EMPTY)
    assert (pod.owner, pod.team) == (None, EMPTY)
    assert (unknown_pod.owner, unknown_pod.team) == (None, None)


def test_the_ask_climbs_the_matrix_from_the_pod_its_decider_was_found_on() -> None:
    po = readiness_decider(_scope(PAY), _matrix())
    sm = readiness_decider(_scope(DATA), _matrix(), "pod-data")

    # The product owner's: to the pod's scrum master, then to its manager.
    assert _climbed(po.owner, po.team, 2) == "Priya"
    assert _climbed(po.owner, po.team, 4) == "Mark"
    # The scrum master's: past their own level, to the manager.
    assert _climbed(sm.owner, sm.team, 2) is None
    assert _climbed(sm.owner, sm.team, 4) == "Max"


async def test_with_no_owner_recorded_the_asks_go_to_the_product_owner_of_the_pods() -> None:
    """As on a tenant whose projects record no owner and whose matrix names no decision
    owner: the product owner, found by their role and their pods as the console's Today
    and Delivery find them."""
    registry = await a_full_day()
    await registry.escalation_matrix_service().save(default_matrix(TENANT, "checkout"), actor="a")
    graph = registry.graph_repository()
    asha = await graph.get_node(TENANT, "dev-asha")
    assert asha is not None
    await graph.upsert_node(replace(asha, metadata={**asha.metadata, "app_roles": "po"}))

    report = await readiness_report(registry=registry)

    asks = next(
        section for section in report.sections if section.title == "What we need, and from whom"
    )
    deciders = {
        group.heading
        for group in asks.groups
        for line in group.lines
        if line.startswith("Decision: Checkout Revamp:") or line.startswith("Decision: Payments")
    }
    assert deciders == {"Asha"}
    assert "Nobody named yet" not in {group.heading for group in asks.groups}


async def test_a_project_owner_named_by_chat_id_decides() -> None:
    registry = await a_full_day()
    await registry.escalation_matrix_service().save(default_matrix(TENANT, "checkout"), actor="a")
    graph = registry.graph_repository()
    omar = await graph.get_node(TENANT, "dev-omar")
    project = await graph.get_node(TENANT, "checkout")
    assert omar is not None and project is not None
    await graph.upsert_node(replace(omar, metadata={**omar.metadata, "chat_external_id": "U-OMAR"}))
    await graph.upsert_node(replace(project, metadata={**project.metadata, "owner_id": "U-OMAR"}))

    report = await readiness_report(registry=registry)

    asks = next(
        section for section in report.sections if section.title == "What we need, and from whom"
    )
    omars = next(group for group in asks.groups if group.heading == "Omar")
    assert sum(line.startswith("Decision: ") for line in omars.lines) == 2
