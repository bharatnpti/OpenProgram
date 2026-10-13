"""What a response says about people to a reader who sees no names (D87, D98).

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
- a person's own status, in their words ("owner_status_summary"), is left out.

Everyone else gets the response as it was built. The route decides by the
principal, never the console.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel

from api.dependencies import get_current_principal, get_registry
from core.application.people_words import PeopleWords, hides_names, people_words
from core.domain.auth import Principal
from core.domain.graph import EdgeKind

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
#: A person's own status, in their words and by its source: an executive reads none (D87).
_LEFT_OUT = frozenset({"owner_status_summary", "owner_status_source", "owner_status_as_of"})


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
    graph = get_registry(request).graph_repository()
    day = _as_of(request.query_params.get("as_of"))
    nodes = await graph.list_nodes(principal.tenant_id, as_of=day)
    edges = await graph.list_edges(principal.tenant_id, kind=EdgeKind.CONTAINS)
    return people_words(nodes, edges, as_of=day)


PeopleWordsDep = Annotated[PeopleWords | None, Depends(get_people_words)]


def for_reader[M: BaseModel](model: M, words: PeopleWords | None) -> M:
    """``model`` as this reader may read it: unchanged unless ``words`` is given."""
    if words is None:
        return model
    return _model(model, words)


def all_for_reader[M: BaseModel](models: list[M], words: PeopleWords | None) -> list[M]:
    return [for_reader(model, words) for model in models]


def _model[M: BaseModel](model: M, words: PeopleWords) -> M:
    values = dict(model)
    update: dict[str, object] = {}
    for name, value in values.items():
        new = _field(name, value, values, words)
        if new is not value:
            update[name] = new
    return model.model_copy(update=update) if update else model


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
