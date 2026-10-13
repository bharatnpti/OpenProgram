"""Who reads people by name, and how an executive reads them instead (D87, D98, D99)."""

from __future__ import annotations

from datetime import date

from core.application.people_words import (
    SMALL_GROUP,
    SMALL_GROUP_LINE,
    PeopleWords,
    hides_names,
    is_small,
    people_words,
    role_word,
    small_group_line,
)
from core.domain.auth import Principal, Role
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod

T = "demo"
DAY = date(2026, 10, 7)


def _principal(*roles: Role) -> Principal:
    return Principal(tenant_id=T, subject="someone", roles=frozenset(roles))


def _words() -> PeopleWords:
    nodes = [
        Developer(tenant_id=T, id="U1", name="Mina Patel", metadata={"app_roles": "po"}),
        Developer(tenant_id=T, id="U2", name="Ira Novak", metadata={"app_roles": "sm"}),
        Developer(tenant_id=T, id="U3", name="Asha Rao", metadata={"app_roles": "mgr,admin"}),
        Developer(
            tenant_id=T,
            id="U4",
            name="Liam Chen",
            metadata={"app_roles": "dev", "chat_external_id": "W0CHAT0LIAM1"},
        ),
        Developer(tenant_id=T, id="U5", name="Zoe Almeida"),
        Developer(tenant_id=T, id="U6", name="Kai Thompson"),
        Developer(tenant_id=T, id="U7", name="Kai Novak"),
        Developer(tenant_id=T, id="U8", name="Noah Weber"),
        Pod(tenant_id=T, id="pod-pay", name="Payments Pod"),
        Pod(tenant_id=T, id="pod-web", name="Storefront Pod"),
    ]
    edges = [
        GraphEdge(tenant_id=T, from_node_id=pod, to_node_id=member, kind=EdgeKind.CONTAINS)
        for pod, member in (
            ("pod-pay", "U4"),
            ("pod-web", "U4"),
            ("pod-web", "U5"),
            ("pod-pay", "U6"),
            ("pod-pay", "U7"),
            ("pod-pay", "U2"),
        )
    ]
    ended = GraphEdge(
        tenant_id=T,
        from_node_id="pod-pay",
        to_node_id="U8",
        kind=EdgeKind.CONTAINS,
        valid_to=date(2026, 9, 1),
    )
    return people_words(nodes, [*edges, ended], as_of=DAY)


def test_only_an_executive_on_their_own_reads_no_names() -> None:
    assert hides_names(_principal(Role.EXEC))
    for role in (Role.ADMIN, Role.MGR, Role.PO, Role.SM, Role.DEV):
        assert not hides_names(_principal(role)), role
        # An executive who also holds a role that reads people reads them as that role.
        assert not hides_names(_principal(Role.EXEC, role)), role


def test_a_role_holder_is_their_role_and_a_developer_their_team() -> None:
    words = _words()

    assert words.label("U1") == "the product owner"
    assert words.label("U2") == "the scrum master"  # a role wins over the pod they are in
    assert words.label("U3") == "the manager"
    # In two pods: the first by name. By chat id too.
    assert words.label("U4") == "Payments Pod"
    assert words.label("W0CHAT0LIAM1") == "Payments Pod"
    assert words.label("U5") == "Storefront Pod"
    # Out of every pod on the day (their link ended): a team member.
    assert words.label("U8") == "a team member"
    # By name when a field holds only a name; nobody known: a team member.
    assert words.label(None, "Zoe Almeida") == "Storefront Pod"
    assert words.label("jira-account-9", "Someone Else") == "a team member"
    assert role_word({"app_roles": "exec"}) == "the executive"
    assert role_word({}) is None


def test_text_says_each_name_as_the_role_or_team() -> None:
    words = _words()

    assert (
        words.say("Committed for Tue 15 Dec 2026 by Mina Patel.")
        == "Committed for Tue 15 Dec 2026 by the product owner."
    )
    # A sentence's start gets a capital; a possessive keeps its 's.
    assert (
        words.say("Mina Patel moved it. Then Liam Chen's review landed.")
        == "The product owner moved it. Then Payments Pod's review landed."
    )
    # A first name one member has is said too; one two members share is left alone.
    assert words.say("Zoe asked for input.") == "Storefront Pod asked for input."
    assert words.say("Kai asked.") == "Kai asked."
    assert words.say("Kai Thompson asked.") == "Payments Pod asked."
    # Issue keys and words that only contain a name stay.
    assert words.say("CHK-12 Asha-flow Minarets") == "CHK-12 Asha-flow Minarets"
    # A bullet's start gets a capital too.
    assert words.say("Verdict.\n- Asha Rao must act.") == "Verdict.\n- The manager must act."


def test_a_group_of_fewer_than_five_is_small_and_says_where_it_is_counted() -> None:
    assert SMALL_GROUP == 5
    assert [is_small(people) for people in (0, 1, 4, 5, 9)] == [True, True, True, False, False]
    assert small_group_line("project") == SMALL_GROUP_LINE
    assert small_group_line("project").endswith("counted with the project's.")
    assert small_group_line("program").endswith("counted with the program's.")
    assert small_group_line(None) == "Fewer than 5 people, so no check-in counts are shown for it."
