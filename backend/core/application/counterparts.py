"""Who a check-in reply can ask something of: the tenant's own members.

A cross-person request DMs the person it names, so the only people a name can
resolve to are the members OpenProgram manages for the tenant. The org
directory (every account in the chat workspace) is a lookup table for adding
members, never a pool of counterparts: a duplicate account, a guest, or an
outsider who joined the workspace is never offered as a candidate and is
never DMed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from core.domain.directory import DirectoryUser
from core.domain.graph import GraphNode, NodeKind
from core.domain.status import CrossPersonMention
from core.ports.directory import DirectoryUserRepository
from core.ports.repositories import GraphRepository, IdentityLinkRepository

_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_WORD = re.compile(r"[^\W_]+")
# An id as typed or as a chat mention leaves it: "U123", "@U123", "dev-1".
_REFERENCE = re.compile(r"\w(?:[\w.-]*\w)?")


@dataclass(frozen=True, kw_only=True)
class MemberContact:
    """A member a request can name: how to reach them and what they are called.

    ``chat_id`` is where a counterpart DM goes: the identity link's chat id,
    else the member id (members added from the directory are keyed by it).
    """

    member_id: str
    chat_id: str
    name: str
    email: str | None = None
    handle: str | None = None


class MemberDirectory:
    """The tenant's active members, as candidates for a named counterpart."""

    def __init__(
        self,
        *,
        graph_repository: GraphRepository | None,
        identity_link_repository: IdentityLinkRepository | None = None,
        directory_repository: DirectoryUserRepository | None = None,
    ) -> None:
        self._graph_repository = graph_repository
        self._identity_link_repository = identity_link_repository
        self._directory_repository = directory_repository

    async def active_members(self, tenant_id: str) -> tuple[MemberContact, ...]:
        """Every member, minus those whose chat account has been deactivated.

        A member with no directory entry (configured by hand) still counts; one
        whose directory entry says the account is gone does not, since nothing
        can reach them any more.
        """
        if self._graph_repository is None:
            return ()
        nodes = await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER)
        chat_ids: dict[str, str] = {}
        if self._identity_link_repository is not None:
            for link in await self._identity_link_repository.list_identity_links(tenant_id):
                chat_id = _clean(link.chat_user_id)
                if chat_id is not None:
                    chat_ids[link.developer_id] = chat_id
        members: list[MemberContact] = []
        for node in sorted(nodes, key=lambda item: item.id):
            chat_id = chat_ids.get(node.id, node.id)
            user = await self._directory_user(tenant_id, chat_id, node.id)
            if user is not None and not user.is_active:
                continue
            members.append(_member_contact(node, chat_id, user))
        return tuple(members)

    async def _directory_user(
        self, tenant_id: str, chat_id: str, member_id: str
    ) -> DirectoryUser | None:
        if self._directory_repository is None:
            return None
        user = await self._directory_repository.get(tenant_id, chat_id)
        if user is None and member_id != chat_id:
            user = await self._directory_repository.get(tenant_id, member_id)
        return user


def members_for_mention(
    mention: CrossPersonMention,
    members: Sequence[MemberContact],
) -> tuple[MemberContact, ...]:
    """The members a mention can mean; empty when it names nobody we manage.

    An email is exact: one that belongs to no member resolves to nobody, even
    when the name matches a member, because the requester named that address.
    """
    email = first_email(mention.email) or first_email(mention.raw_name)
    if email is not None:
        return members_with_email(email, members)
    by_reference = members_by_reference(mention.raw_name, members)
    if by_reference:
        return by_reference
    return members_by_name(mention.raw_name, members)


def members_with_email(email: str, members: Sequence[MemberContact]) -> tuple[MemberContact, ...]:
    wanted = email.casefold()
    return tuple(member for member in members if member.email and member.email.casefold() == wanted)


def members_by_reference(text: str, members: Sequence[MemberContact]) -> tuple[MemberContact, ...]:
    """Members named by their chat or member id, for example ``@U123`` or ``U123``."""
    tokens = {token.casefold() for token in _REFERENCE.findall(text)}
    if not tokens:
        return ()
    return tuple(
        member
        for member in members
        if member.chat_id.casefold() in tokens or member.member_id.casefold() in tokens
    )


def members_by_name(query: str, members: Sequence[MemberContact]) -> tuple[MemberContact, ...]:
    """Members whose name or handle a name refers to.

    An exact name or handle wins. Otherwise every word of the query has to
    start a word of the member's name, so "Noah" and "Noah W" find Noah Weber
    while "Ira" does not find Mira.
    """
    words = _words(query)
    if not words:
        return ()
    exact = tuple(
        member
        for member in members
        if _words(member.name) == words or (member.handle and _words(member.handle) == words)
    )
    if exact:
        return exact
    return tuple(member for member in members if _starts_words(words, _words(member.name)))


def member_named_in_answer(
    answer: str,
    asked: CrossPersonMention,
    candidates: Sequence[MemberContact],
    members: Sequence[MemberContact],
) -> MemberContact | None:
    """The one member an answer to "who did you mean?" picks, else None.

    A chat mention or an email settles it when it names exactly one member,
    whoever the question offered. Otherwise the answer's words pick among the
    offered candidates, a full name before a first name, and the pick has to
    rest on a word the asked name did not have: "Alexa" or "Alex Chen" settles
    an ambiguous "Alex", repeating "Alex" does not. Two members with the same
    name stay unsettled until the answer gives an address or a mention. With
    no candidates offered ("I could not find ..."), only a member's full name
    counts, so a first name dropped in passing never settles a request.
    """
    named: dict[str, MemberContact] = {
        member.member_id: member for member in members_by_reference(answer, members)
    }
    for email in all_emails(answer):
        named.update({member.member_id: member for member in members_with_email(email, members)})
    if named:
        return next(iter(named.values())) if len(named) == 1 else None
    words = set(_words(answer))
    new_words = words - set(_words(asked.raw_name))
    if not new_words:
        return None
    pool = candidates or members
    full = [member for member in pool if _words(member.name) and set(_words(member.name)) <= words]
    first = [
        member for member in candidates if _words(member.name) and _words(member.name)[0] in words
    ]
    picked = full or first
    if len(picked) != 1 or not set(_words(picked[0].name)) & new_words:
        return None
    return picked[0]


def first_email(text: str | None) -> str | None:
    """The first email address in a piece of text, if there is one."""
    if not text:
        return None
    match = _EMAIL.search(text)
    return match.group(0) if match else None


def all_emails(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _EMAIL.finditer(text))


def name_words(text: str) -> tuple[str, ...]:
    """A name as lower-cased words, for comparing two ways of writing it."""
    return _words(text)


def _words(text: str) -> tuple[str, ...]:
    flat = text.casefold().replace("'s ", " ").removesuffix("'s")
    return tuple(_WORD.findall(flat))


def _starts_words(query: tuple[str, ...], name: tuple[str, ...]) -> bool:
    return bool(name) and all(any(word.startswith(part) for word in name) for part in query)


def _member_contact(node: GraphNode, chat_id: str, user: DirectoryUser | None) -> MemberContact:
    name = node.name.strip() or (user.display_name.strip() if user is not None else "")
    return MemberContact(
        member_id=node.id,
        chat_id=chat_id,
        name=name or node.id,
        email=_metadata_string(node, "email") or (_clean(user.email) if user else None),
        handle=_metadata_string(node, "handle") or (_clean(user.handle) if user else None),
    )


def _metadata_string(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return _clean(value) if isinstance(value, str) else None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
