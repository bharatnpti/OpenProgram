"""Display names for the people a read model mentions -- never a raw chat id.

A person reaches the read models as an id: a member's node id, which in a
single-workspace tenant is also their chat user id. A view that shows the
person to a reader names them from the member record first, then the chat
directory, and otherwise as ``UNKNOWN_PERSON``.

Text stored before a builder named people can still hold their ids (an
inferred status summary said "risks flagged on U0..."). ``without_member_ids``
is the read-time cleanup for it: each member id the tenant knows becomes that
member's name, and any other token shaped like a chat user id becomes
``UNKNOWN_PERSON``. An id of another shape that no member has is left as it is.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence

from core.domain.graph import GraphNode, NodeKind
from core.ports.directory import DirectoryUserRepository
from core.ports.repositories import GraphRepository

# Who a line is about when nothing names them: never a raw chat id.
UNKNOWN_PERSON = "a team member"

# A chat user id as the chat provider issues them: U or W, then 8 to 11
# capitals and digits with at least one digit ("U0000TEST01"). A word in
# capitals ("UNDERSTOOD") has no digit; an issue key ("CHK-12") has a dash.
CHAT_ID = re.compile(r"(?<![\w/-])[UW](?=[A-Z0-9]*\d)[A-Z0-9]{8,11}(?![\w/-])")


class PersonNames:
    """Look up display names for person ids: the member's name, then the directory's.

    Either source may be absent; with neither, no id resolves.
    """

    def __init__(
        self,
        graph_repository: GraphRepository | None = None,
        directory_repository: DirectoryUserRepository | None = None,
    ) -> None:
        self._graph_repository = graph_repository
        self._directory_repository = directory_repository

    async def resolve(self, tenant_id: str, person_ids: Iterable[str]) -> dict[str, str]:
        """Map each id a member record or the chat directory names to that name.

        An id neither source names is left out, so a caller can still use a
        name recorded with its own data before falling back to
        ``UNKNOWN_PERSON``. A "name" that is only one of the person's ids does
        not count as a name.
        """
        wanted = {person_id.strip() for person_id in person_ids if person_id.strip()}
        names: dict[str, str] = {}
        if not wanted:
            return names
        if self._graph_repository is not None:
            members = _members_by_id(
                await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
            )
            for person_id in wanted:
                member = members.get(person_id)
                if member is None:
                    continue
                name = _usable_name(member.name, raw_ids={person_id, *_member_ids(member)})
                if name is not None:
                    names[person_id] = name
        if self._directory_repository is not None:
            for person_id in sorted(wanted - names.keys()):
                user = await self._directory_repository.get(tenant_id, person_id)
                if user is None:
                    continue
                name = _usable_name(user.display_name, raw_ids={person_id, user.external_id})
                if name is not None:
                    names[person_id] = name
        return names

    async def by_member_id(self, tenant_id: str) -> dict[str, str]:
        """Every member's name by each of their ids: node id and chat id (``member_names``)."""
        if self._graph_repository is None:
            return {}
        return member_names(await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER))


def member_names(members: Iterable[GraphNode]) -> dict[str, str]:
    """Each member's name by node id and by chat id; one whose name is only an id is left out.

    A member's own node id wins over another member's chat id.
    """
    listed = [member for member in members if member.kind is NodeKind.DEVELOPER]
    names: dict[str, str] = {}
    for member in listed:
        chat_id = _chat_id(member)
        name = _usable_name(member.name, raw_ids=_member_ids(member))
        if chat_id is not None and name is not None:
            names.setdefault(chat_id, name)
    for member in listed:
        name = _usable_name(member.name, raw_ids=_member_ids(member))
        if name is not None:
            names[member.id] = name
    return names


def without_member_ids(text: str, names: Mapping[str, str]) -> str:
    """``text`` with each member id in ``names`` replaced by the name, any other chat id by
    ``UNKNOWN_PERSON``.

    ``names`` maps an id to a display name (``member_names``). Only ids that
    look like ids are replaced (a digit, a dash, an underscore or a colon in
    them), so a member whose id is a plain word never rewrites that word. "Ana
    (U0...)" becomes "Ana", not the name twice.
    """
    if not text:
        return text
    for raw_id, name in sorted(names.items(), key=lambda item: -len(item[0])):
        if raw_id not in text or raw_id == name or not _looks_like_an_id(raw_id):
            continue
        escaped = re.escape(raw_id)
        # A literal, so a backslash in a name is never read as a group reference.
        literal = name.replace("\\", "\\\\")
        text = re.sub(rf"{re.escape(name)}\s*[(\[]\s*`?{escaped}`?\s*[)\]]", literal, text)
        text = re.sub(rf"(?<![\w/-])(`?){escaped}\1(?![\w/-])", literal, text)
    return CHAT_ID.sub(UNKNOWN_PERSON, text)


def _looks_like_an_id(value: str) -> bool:
    """Ids such as U0AA1OMAR01, U1001 or dev-ada, not a word or a bare number."""
    return (
        len(value) >= 4
        and " " not in value
        and not value.isdigit()
        and any(char.isdigit() or char in "-_:" for char in value)
    )


def person_name(names: Mapping[str, str], person_id: str | None, recorded: str | None) -> str:
    """The resolved name, else the name recorded with the data, else ``UNKNOWN_PERSON``."""
    if person_id and (name := names.get(person_id.strip())):
        return name
    raw_ids = {person_id.strip()} if person_id else set()
    return _usable_name(recorded, raw_ids=raw_ids) or UNKNOWN_PERSON


def _usable_name(name: str | None, *, raw_ids: Collection[str]) -> str | None:
    cleaned = (name or "").strip()
    if not cleaned or cleaned in raw_ids:
        return None
    return cleaned


def _members_by_id(members: Sequence[GraphNode]) -> Mapping[str, GraphNode]:
    """Members by node id, and by chat id for members whose node id differs.

    A member's own node id wins over another member's chat id.
    """
    by_id: dict[str, GraphNode] = {}
    for member in members:
        chat_id = _chat_id(member)
        if chat_id is not None:
            by_id.setdefault(chat_id, member)
    by_id.update({member.id: member for member in members})
    return by_id


def _member_ids(member: GraphNode) -> set[str]:
    chat_id = _chat_id(member)
    return {member.id} if chat_id is None else {member.id, chat_id}


def _chat_id(member: GraphNode) -> str | None:
    value = member.metadata.get("chat_external_id")
    return value.strip() if isinstance(value, str) and value.strip() else None
