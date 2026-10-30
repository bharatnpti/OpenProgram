"""What a response says about people to a reader who sees no names.

The Project page, Today and the Portfolio read the same DTOs for every role.
For an executive (``people_words.hides_names``) each of those routes passes its
response through ``for_reader`` before it goes out, and the response then names
nobody:

- a field that names a person ("changed_by_name", "asked_by_name",
  "assignee_name", ...) holds the person's role or owning team instead
  ("the product owner", "Payments Pod"), else "a team member";
- a field that holds a member's id ("changed_by", "set_by", "owner_id", ...)
  holds the same words, so no id can be looked up to a name either;
- a map of ids to names ("actor_names", "source_names") says each person the
  same way and leaves work items as they are;
- every other piece of text (reasons, notes, summaries) says each member's
  name as their role or team;
- a person's own status, in their words ("owner_status_summary"), is left out;
- a directory item (a program, project, workstream or pod) lists no member, names
  no owner, TPM or scrum master, and gives a pod's escalation contacts as the role
  they hold ("the scrum master", "the manager"), with no member or chat id;
- a feed event about a person has no entity reference (it would hold their member
  id) and says who it is about as the person's stand-in, in its details too;
- a review-flow scope of fewer than five people has no member count.

Everyone else gets the response as it was built. The route decides by the
principal, never the console.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from enum import Enum
from typing import Annotated, cast

from fastapi import Depends, Request
from pydantic import BaseModel

from api.dependencies import get_current_principal, get_registry
from api.dtos import DirectoryItemResponse, PortfolioFeedItemResponse, PullRequestFlowScopeDto
from core.application.attention import TeamGraph
from core.application.config_service import PERSON_METADATA_KEYS
from core.application.people_words import (
    ROLE_WORDS,
    PeopleWords,
    hides_names,
    is_small,
    people_words,
)
from core.application.portfolio_privacy import small_groups
from core.domain.auth import Principal
from core.domain.escalation import (
    ESCALATION_MEMBER_METADATA_KEYS,
    ESCALATION_METADATA_KEYS,
    EscalationTarget,
)
from core.domain.graph import EdgeKind, NodeKind

#: Fields that hold a person's id: said as their role or team when they are a member.
_ID_FIELDS = frozenset(
    {
        "changed_by",
        "set_by",
        "asked_by",
        "asked_to",
        "by",
        "created_by",
        "signed_by",
        "owner_id",
        "dismissed_by",
        "decided_by",
        "updated_by",
    }
)
#: Fields that hold a person's name, each with the field holding that person's id.
_NAME_FIELDS: dict[str, str | None] = {
    "changed_by_name": "changed_by",
    "set_by_name": "set_by",
    "asked_by_name": "asked_by",
    "asked_to_name": "asked_to",
    "by_name": "by",
    "created_by_name": "created_by",
    "updated_by_name": "updated_by",
    "person_name": None,
    "assignee_name": "assignee_id",
    "owner_name": "owner_id",
    "author_name": None,
    "developer_name": "developer_id",
}
#: Maps of node ids to names: a person's entry is said as their role or team.
_NAME_MAPS = frozenset({"actor_names", "source_names"})
#: A person's own status, in their words and by its source: an executive reads none.
_LEFT_OUT = frozenset(
    {"owner_status_summary", "owner_status_source", "owner_status_as_of", "owner_says"}
)
#: The people a feed event's details name: each id, with the field holding their name.
_FEED_PEOPLE: dict[str, str] = {
    "reporter_id": "reporter_name",
    "referenced_person_id": "referenced_person_name",
}
#: A pod's escalation contacts as an executive reads them: the role, never the person.
_CONTACT_WORDS: dict[EscalationTarget, str] = {
    EscalationTarget.SCRUM_MASTER: ROLE_WORDS["sm"],
    EscalationTarget.MANAGER: ROLE_WORDS["mgr"],
}


async def get_people_words(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PeopleWords | None:
    """The stand-ins for every member, for a reader who hides names; None for anyone else.

    Read for the request's ``as_of`` when it has one, so a past day's pods say
    who was in which team then.
    """
    if not hides_names(principal):
        return None
    registry = get_registry(request)
    graph = registry.graph_repository()
    day = _as_of(request.query_params.get("as_of"))
    nodes = await graph.list_nodes(principal.tenant_id, as_of=day)
    edges = await graph.list_edges(principal.tenant_id, kind=EdgeKind.CONTAINS)
    words = people_words(nodes, edges, as_of=day)
    # The small groups of the day asked for, else of the tenant's today: the day the
    # config routes link a pod on (not the server's date.today()).
    team = TeamGraph.from_graph(nodes, edges, day or registry.tenant_today())
    return replace(words, small_groups=frozenset(small_groups(team)))


PeopleWordsDep = Annotated[PeopleWords | None, Depends(get_people_words)]


def for_reader[M: BaseModel](model: M, words: PeopleWords | None) -> M:
    """``model`` as this reader may read it: unchanged unless ``words`` is given."""
    if words is None:
        return model
    return _model(model, words)


def all_for_reader[M: BaseModel](models: list[M], words: PeopleWords | None) -> list[M]:
    return [for_reader(model, words) for model in models]


def _model[M: BaseModel](model: M, words: PeopleWords) -> M:
    if isinstance(model, DirectoryItemResponse):
        return cast("M", _directory_item(model, words))
    if isinstance(model, PortfolioFeedItemResponse):
        model = cast("M", _feed_item(model, words))
    values = dict(model)
    update: dict[str, object] = {}
    for name, value in values.items():
        new = _field(name, value, values, words)
        if new is not value:
            update[name] = new
    seen = model.model_copy(update=update) if update else model
    if isinstance(seen, PullRequestFlowScopeDto):
        return cast("M", _flow_scope(seen))
    return seen


def _field(name: str, value: object, siblings: dict[str, object], words: PeopleWords) -> object:
    if name in _LEFT_OUT:
        return None
    if isinstance(value, BaseModel):
        return _model(value, words)
    if isinstance(value, list):
        items = [_field(name, item, siblings, words) for item in value]
        return items if any(a is not b for a, b in zip(items, value, strict=True)) else value
    if isinstance(value, dict):
        if name in _NAME_MAPS:
            return {
                key: words.label(key, item) if words.is_person(key) else _text(item, words)
                for key, item in value.items()
            }
        return {key: _field(name, item, siblings, words) for key, item in value.items()}
    if isinstance(value, Enum) or not isinstance(value, str):
        return value
    if name in _ID_FIELDS:
        return words.label(value) if words.is_person(value) else value
    if name in _NAME_FIELDS:
        pair = _NAME_FIELDS[name]
        person = siblings.get(pair) if pair else None
        return words.label(person if isinstance(person, str) else None, value)
    return _text(value, words)


def _feed_item(item: PortfolioFeedItemResponse, words: PeopleWords) -> PortfolioFeedItemResponse:
    """A feed event with no member id: not in its entity, nor in the people its details name.

    An event about a person (a check-in, a request they opened) has that person as
    its entity, so it has none; the reader gets who it is about from ``person_name``.
    """
    details = dict(item.details)
    for id_key, name_key in _FEED_PEOPLE.items():
        person, named = details.get(id_key), details.get(name_key)
        said = words.label(
            person if isinstance(person, str) else None, named if isinstance(named, str) else None
        )
        if isinstance(person, str):
            details[id_key] = said
        if isinstance(named, str):
            details[name_key] = said
    about_a_person = item.entity_ref is not None and item.entity_ref.kind is NodeKind.DEVELOPER
    return item.model_copy(
        update={"details": details, "entity_ref": None if about_a_person else item.entity_ref}
    )


def _flow_scope(scope: PullRequestFlowScopeDto) -> PullRequestFlowScopeDto:
    """A review-flow scope of fewer than five people counts none of them.

    Its requests, stages and dates stay; its people are counted in the group above.
    """
    if scope.member_count is not None and is_small(scope.member_count):
        return scope.model_copy(update={"member_count": None})
    return scope


def _directory_item(item: DirectoryItemResponse, words: PeopleWords) -> DirectoryItemResponse:
    """A program, project, workstream or pod with none of its people.

    The members it holds and the people its metadata names (owner, TPM, scrum
    master) are left out, not stood in for: counting them is the small-group
    rule's and the directory has no count. A pod's escalation contacts
    stay as the role ("the scrum master"), so it still says it has them.
    """
    return item.model_copy(
        update={
            "description": _said(item.description, words),
            "metadata": _directory_metadata(item.metadata, words),
            "member_ids": [],
            "people": [],
        }
    )


def _directory_metadata(
    metadata: Mapping[str, str | int | float | bool | None], words: PeopleWords
) -> dict[str, str | int | float | bool | None]:
    kept = dict(metadata)
    for target, role in _CONTACT_WORDS.items():
        chat_key, name_key = ESCALATION_METADATA_KEYS[target]
        member_key = ESCALATION_MEMBER_METADATA_KEYS[target]
        has_contact = any(_is_set(kept.get(key)) for key in (chat_key, name_key, member_key))
        kept.pop(chat_key, None)
        kept.pop(member_key, None)
        if has_contact:
            kept[name_key] = role
    for key in PERSON_METADATA_KEYS:
        kept.pop(key, None)
    for key, value in list(kept.items()):
        # A person typed under a key this does not know is still a person.
        if isinstance(value, str) and words.is_person(" ".join(value.split())):
            del kept[key]
        elif key == "description":
            kept[key] = _said(value, words)
    return kept


def _is_set(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _said(value: object, words: PeopleWords) -> str | None:
    """A description with every member's name said as their stand-in."""
    return words.say(value) if isinstance(value, str) else None


def _text(value: object, words: PeopleWords) -> object:
    if not isinstance(value, str) or isinstance(value, Enum):
        return value
    said = words.say(value)
    return value if said == value else said


def _as_of(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["PeopleWordsDep", "all_for_reader", "for_reader", "get_people_words"]
