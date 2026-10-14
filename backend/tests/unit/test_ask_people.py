"""Ask's tools for an executive say each person as the Project page does.

A manager's Ask reads names; an executive's reads the role ("the product
owner") or the owning team ("Payments Pod") in every tool result, with no
person's node, own status or id that leads to a name, and no People & roles.
"""

from __future__ import annotations

import json

from core.application.ask_people import NamelessTool, said_without_names
from core.application.people_words import PeopleWords
from core.domain.auth import Role
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_ask_service import (
    _HANA,
    _IRA,
    _LIAM,
    _OMAR,
    _ZOE,
    AS_OF,
    _ask_service,
    _attention_store,
    _llm_response,
    _principal,
    _tool,
)

NAMES = ("Omar", "Haddad", "Ira", "Novak", "Hana", "Kobayashi", "Zoe", "Almeida", "Liam", "Chen")
IDS = (_OMAR, _IRA, _HANA, _ZOE, _LIAM)


def _named(text: str) -> list[str]:
    return [word for word in (*NAMES, *IDS) if word in text]


def test_a_result_says_every_person_as_their_role_or_team() -> None:
    words = PeopleWords(
        labels={"U1": "the product owner", "U2": "Payments Pod", "C-2": "Payments Pod"},
        by_name={"Mina Patel": "the product owner", "Liam Chen": "Payments Pod"},
    )
    result = json.dumps(
        {
            "owner": {"id": "U2", "name": "Liam Chen"},
            "says": "Mina Patel asked Liam Chen for a review.",
            "by_person": {"U1": 2, "pod-pay": 1},
            "matches": [
                {"id": "U2", "kind": "developer", "name": "Liam Chen", "metadata": {"tz": "x"}},
                {"id": "C-2", "kind": "developer", "name": "Liam Chen", "metadata": {}},
                {"id": "pod-pay", "kind": "pod", "name": "Payments Pod", "metadata": {"a": 1}},
            ],
        }
    )

    said = json.loads(said_without_names(result, words))

    assert said["owner"] == {"id": "Payments Pod", "name": "Payments Pod"}
    assert said["says"] == "The product owner asked Payments Pod for a review."
    assert said["by_person"] == {"the product owner": 2, "pod-pay": 1}
    # A person's node keeps no metadata, and two members of one team read once.
    assert said["matches"] == [
        {"id": "Payments Pod", "kind": "developer", "name": "Payments Pod"},
        {"id": "pod-pay", "kind": "pod", "name": "Payments Pod", "metadata": {"a": 1}},
    ]
    # Plain text is said the same way.
    assert said_without_names("Liam Chen is blocked.", words) == "Payments Pod is blocked."


async def test_an_executives_tools_name_nobody_and_a_managers_keep_the_names() -> None:
    store = await _attention_store()
    service = _ask_service(store, FakeLlmProvider())
    executive, manager = _principal(Role.EXEC), _principal(Role.MGR)

    tools = service.tools_for(executive, AS_OF)
    assert tools and all(isinstance(tool, NamelessTool) for tool in tools)
    assert "team_profiles" not in {tool.name for tool in tools}
    assert not any(isinstance(tool, NamelessTool) for tool in service.tools_for(manager, AS_OF))

    reasons = await _tool(service, executive, "status_reasons").run({"node_id": "program-platform"})
    search = await _tool(service, executive, "search_graph_nodes").run({"query": "a"})
    neighbours = await _tool(service, executive, "graph_neighbors").run({"node_id": "pod-payments"})
    for result in (reasons, search, neighbours):
        assert _named(result) == [], result
    payload = json.loads(reasons)
    # The tree an executive reads (/programs/{id}/tree): no person's own status,
    # a blocker as an open blocker on its work item, and a non-reply counted nowhere.
    assert payload["reasons"] == [
        {
            "contributes": "amber",
            "kind": "blocker",
            "says": "Open blocker.",
            "about": [{"id": "CHK-8", "kind": "task", "label": "CHK-8"}],
        }
    ]
    assert payload["open_blocker_count"] == 1
    assert "Payments Pod" in search

    named = await _tool(service, manager, "status_reasons").run({"node_id": "program-platform"})
    assert "Omar Haddad" in named


async def test_an_executives_answer_says_a_cited_person_as_their_team() -> None:
    store = await _attention_store()
    reply = json.dumps(
        {"answer": f"Payments Pod is amber: {_OMAR} sent a partial update.", "references": [_OMAR]}
    )
    view = await _ask_service(store, FakeLlmProvider(responses=[_llm_response(text=reply)])).ask(
        principal=_principal(Role.EXEC),
        question="Why is Payments amber?",
        correlation_id="ask-exec",
        as_of=AS_OF,
    )

    assert view.answer == "Payments Pod is amber: Payments Pod sent a partial update."
    assert [(source.kind, source.label) for source in view.sources] == [
        (source.kind, "Payments Pod") for source in view.sources
    ]
    assert _named(view.answer) == []
