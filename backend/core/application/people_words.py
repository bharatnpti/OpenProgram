"""How an executive reads a person: by role or owning team, never by name (D87, D98).

Executives read counts, work items and the team that owns them. Wherever a read
they open names a person (the committed date's "set by", a question's "asked
by", a requirement's assignee, a brief's sentence), they get instead:

- the person's role, when they hold one that acts on the work: "the product
  owner", "the scrum master", "the manager", "the admin", "the executive";
- else their owning team, the pod they are in ("Payments Pod");
- else "a team member".

``hides_names`` decides who reads that way: an executive who holds no role that
reads people (a manager or scrum master who is also an executive keeps names).
Decided on the server, per role, never by the console hiding it.

``SMALL_GROUP`` is the small-group rule (D99, D104): an executive gets no
people counts for a pod, a project or a program of fewer people; its people
are counted in the smallest group above it that is large enough, else nowhere.
Its dates, verdicts and work still show.

Pure: callers pass the nodes and edges they read for the day.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from core.application.person_names import UNKNOWN_PERSON
from core.domain.auth import Principal, Role
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, JsonScalar, NodeKind

#: D99, D104: an executive reads no people counts for a pod, project or program of fewer.
SMALL_GROUP = 5
#: How a group under ``SMALL_GROUP`` reads to an executive, in place of its people
#: counts: where its people are counted instead.
SMALL_GROUP_LINE = (
    f"Fewer than {SMALL_GROUP} people, so its check-ins are counted with the project's."
)
SMALL_GROUP_PROGRAM_LINE = (
    f"Fewer than {SMALL_GROUP} people, so its check-ins are counted with the program's."
)
SMALL_GROUP_ALONE_LINE = f"Fewer than {SMALL_GROUP} people, so no check-in counts are shown for it."

#: The roles that read people by name: everyone but an executive on their own.
_NAME_READERS = (Role.ADMIN, Role.MGR, Role.PO, Role.SM, Role.DEV)

#: A person's role as an executive reads it, the one that sets dates first.
_ROLE_WORDS: tuple[tuple[str, str], ...] = (
    ("po", "the product owner"),
    ("sm", "the scrum master"),
    ("mgr", "the manager"),
    ("admin", "the admin"),
    ("exec", "the executive"),
)
#: The same, by role: "sm" is "the scrum master". For a stand-in that is a role by
#: definition, such as a pod's escalation contact, whoever holds it.
ROLE_WORDS: Mapping[str, str] = dict(_ROLE_WORDS)


def hides_names(principal: Principal) -> bool:
    """Whether this reader gets roles and teams in place of people's names.

    An executive does. Anyone who also holds a role that reads people (admin,
    manager, product owner, scrum master, developer) reads names as that role.
    """
    if not principal.has_role(Role.EXEC):
        return False
    return not any(principal.has_role(role) for role in _NAME_READERS)


def role_word(metadata: Mapping[str, JsonScalar] | None) -> str | None:
    """The role a member acts in, as an executive reads it; None for a developer.

    The roles are the member's ``app_roles`` (``"mgr,admin"``), as sign-in
    reads them.
    """
    value = (metadata or {}).get("app_roles")
    if not isinstance(value, str):
        return None
    roles = {item.strip().lower() for item in value.split(",") if item.strip()}
    for role, words in _ROLE_WORDS:
        if role in roles:
            return words
    return None


@dataclass(frozen=True, kw_only=True)
class PeopleWords:
    """Each member's stand-in (role, else team) and the names it stands in for.

    ``labels`` is keyed by every id a member goes by (node id, chat id);
    ``by_name`` by their display name, for a field that holds only a name.
    """

    labels: Mapping[str, str] = field(default_factory=dict)
    by_name: Mapping[str, str] = field(default_factory=dict)
    #: The pods, projects and programs under ``SMALL_GROUP`` people (D99, D104),
    #: whose people a reader of these words reads no counts of; set by the reader.
    small_groups: frozenset[str] = frozenset()

    def is_person(self, value: str | None) -> bool:
        return bool(value) and (value in self.labels or value in self.by_name)

    def label(self, person: str | None, name: str | None = None) -> str:
        """The stand-in for a person given by id, else by name; "a team member" if neither."""
        if person and person in self.labels:
            return self.labels[person]
        for candidate in (name, person):
            if candidate:
                clean = " ".join(candidate.split())
                if clean in self.by_name:
                    return self.by_name[clean]
        return UNKNOWN_PERSON

    def say(self, text: str) -> str:
        """``text`` with every member's name, and first name, said as their stand-in.

        Whole names go before first names, so "Asha Rao" goes whole before
        "Asha" could; a first name two members share is left alone. A
        possessive keeps its "'s", and a stand-in that opens a sentence starts
        with a capital. Issue keys and every other word stay as they are.
        """
        if not text or not self.by_name:
            return text
        result = text
        for name, words in _spoken(self.by_name):
            pattern = re.compile(rf"(?<![\w-]){re.escape(name)}('s)?(?![\w-])")
            result = pattern.sub(functools.partial(_placed, words=words), result)
        return result


def people_words(
    nodes: Iterable[GraphNode], edges: Iterable[GraphEdge], as_of: date | None = None
) -> PeopleWords:
    """Every member's stand-in, from the tenant's nodes and its ``contains`` edges.

    A member who holds a role is that role; a developer is the pod they are in
    (the first by name when several); one in no pod is "a team member".
    ``edges`` are read for ``as_of`` when it is given.
    """
    listed = list(nodes)
    pods_of = _pods_of(listed, edges, as_of)
    labels: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for node in listed:
        if node.kind is not NodeKind.DEVELOPER:
            continue
        words = role_word(node.metadata)
        if words is None:
            pods = sorted(pods_of.get(node.id, ()))
            words = pods[0] if pods else UNKNOWN_PERSON
        labels[node.id] = words
        chat_id = node.metadata.get("chat_external_id")
        if isinstance(chat_id, str) and chat_id.strip():
            labels.setdefault(chat_id.strip(), words)
        name = " ".join((node.name or "").split())
        if name and name != node.id:
            by_name.setdefault(name, words)
    return PeopleWords(labels=labels, by_name=by_name)


def _pods_of(
    nodes: list[GraphNode], edges: Iterable[GraphEdge], as_of: date | None
) -> dict[str, list[str]]:
    """Each member's pods by name, from the ``contains`` edges in force on ``as_of``."""
    by_id = {node.id: node for node in nodes}
    pods_of: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind is not EdgeKind.CONTAINS or (as_of and not edge.is_active_on(as_of)):
            continue
        pod, member = by_id.get(edge.from_node_id), by_id.get(edge.to_node_id)
        if pod is None or member is None:
            continue
        if pod.kind is NodeKind.POD and member.kind is NodeKind.DEVELOPER:
            pods_of.setdefault(member.id, []).append(pod.name or pod.id)
    return pods_of


def is_small(people: int) -> bool:
    """Whether a group of ``people`` is too small for an executive to read its counts."""
    return people < SMALL_GROUP


def small_group_line(counted_in: str | None) -> str:
    """Where a small group's people are counted, said in its place: "project", "program" or none."""
    if counted_in == "project":
        return SMALL_GROUP_LINE
    if counted_in == "program":
        return SMALL_GROUP_PROGRAM_LINE
    return SMALL_GROUP_ALONE_LINE


def _spoken(by_name: Mapping[str, str]) -> list[tuple[str, str]]:
    """Each name to say otherwise, with its stand-in: whole names, then first
    names only one member has, longest first."""
    owners = Counter(name.split(" ")[0] for name in by_name)
    pairs = dict(by_name)
    for name, words in by_name.items():
        first = name.split(" ")[0]
        if len(first) >= 3 and first != name and owners[first] == 1 and first not in by_name:
            pairs[first] = words
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


def _placed(match: re.Match[str], words: str) -> str:
    """The stand-in where the name stood: capital at a sentence's or a bullet's start,
    its "'s" kept."""
    before = match.string[: match.start()].rstrip(" ")
    opens = not before or before.endswith((".", "!", "?", ":", "\n", "\n-", "\n*"))
    said = words[:1].upper() + words[1:] if opens else words
    return said + (match.group(1) or "")


__all__ = [
    "ROLE_WORDS",
    "SMALL_GROUP",
    "SMALL_GROUP_ALONE_LINE",
    "SMALL_GROUP_LINE",
    "SMALL_GROUP_PROGRAM_LINE",
    "PeopleWords",
    "hides_names",
    "is_small",
    "people_words",
    "role_word",
    "small_group_line",
]
