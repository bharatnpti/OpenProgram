"""Display names for the people a read model mentions -- never a raw chat id.

A person reaches the read models as an id: a member's node id, which in a
single-workspace tenant is also their chat user id. A view that shows the
person to a reader names them from the member record first, then the chat
directory, and otherwise as ``UNKNOWN_PERSON``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence

from core.domain.graph import GraphNode, NodeKind
from core.ports.directory import DirectoryUserRepository
from core.ports.repositories import GraphRepository

# Who a line is about when nothing names them: never a raw chat id.
UNKNOWN_PERSON = "a team member"


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
