"""What Ask's tools hand the model for an executive: roles and teams, never a person.

An executive reads no person's name in the console: the Project page says
each person as their role ("the product owner") or owning team ("Payments
Pod"). Ask's tools read the same data the pages do, so for an executive every
tool result is passed through the same words before the model sees it, and an
answer can only say what the page would:

- a value that is a member's id or chat id is their role or team, so no id can
  be looked up to a name either;
- every other piece of text says each member's name as their role or team;
- a person's own node (``"kind": "developer"``) keeps no metadata (their chat
  id, time zone, title), and the same stand-in twice in one list is said once.

The words are read once per question, on the first tool call. Pure apart from
that one read; decided by the principal (``people_words.hides_names``), never
by the console.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from core.application.people_words import PeopleWords, people_words
from core.domain.graph import EdgeKind, JsonScalar, NodeKind
from core.ports.repositories import GraphRepository
from core.ports.tools import AgentTool

#: The keys that say what kind of node an object of a tool's result is.
_KIND_KEYS = ("kind", "entity_kind", "neighbor_kind")


class AskPeopleWords:
    """The tenant's stand-ins for the day of a question, read on first use."""

    def __init__(self, *, repository: GraphRepository, tenant_id: str, as_of: date) -> None:
        self._repository = repository
        self._tenant_id = tenant_id
        self._as_of = as_of
        self._words: PeopleWords | None = None

    async def get(self) -> PeopleWords:
        if self._words is None:
            nodes = await self._repository.list_nodes(self._tenant_id, as_of=self._as_of)
            edges = await self._repository.list_edges(self._tenant_id, kind=EdgeKind.CONTAINS)
            self._words = people_words(nodes, edges, as_of=self._as_of)
        return self._words


@dataclass(frozen=True, kw_only=True)
class NamelessTool:
    """A tool whose every result says each person as their role or team."""

    inner: AgentTool
    words: AskPeopleWords = field(repr=False)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.inner.parameters

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        return said_without_names(await self.inner.run(arguments), await self.words.get())


def said_without_names(result: str, words: PeopleWords) -> str:
    """A tool's result with every person said as their role or team."""
    try:
        data = json.loads(result)
    except ValueError:
        return words.say(result)
    return json.dumps(_value(data, words), ensure_ascii=False)


def _value(value: object, words: PeopleWords) -> object:
    if isinstance(value, dict):
        return _object(value, words)
    if isinstance(value, list):
        items = [_value(item, words) for item in value]
        return _once(items) if any(_is_person(item) for item in value) else items
    if isinstance(value, str):
        return words.label(value) if value in words.labels else words.say(value)
    return value


def _object(value: dict[str, object], words: PeopleWords) -> dict[str, object]:
    person = value.get("kind") == NodeKind.DEVELOPER.value
    said: dict[str, object] = {}
    for key, item in value.items():
        if person and key == "metadata":
            continue
        name = words.label(key) if key in words.labels else key
        said.setdefault(name, _value(item, words))
    return said


def _is_person(value: object) -> bool:
    return isinstance(value, dict) and any(
        value.get(key) == NodeKind.DEVELOPER.value for key in _KIND_KEYS
    )


def _once(items: list[object]) -> list[object]:
    """The list with a person's stand-in said once: two members of one team read alike."""
    seen: set[str] = set()
    kept: list[object] = []
    for item in items:
        if _is_person(item):
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
        kept.append(item)
    return kept


__all__ = ["AskPeopleWords", "NamelessTool", "said_without_names"]
