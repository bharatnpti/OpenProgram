"""What a check-in's "Got it" ack says about the asks its reply made of others.

A reply that asks someone for a review, input or a dependency opens a
cross-person request and DMs that person. The requester used to hear only
"your update is recorded" and learned who had been asked when that person
answered. The ack now names who was asked and for what, or says plainly why
nobody was: no member by that name, a member with no chat id, a DM that did
not go out, or an open request of theirs that already asks it.

Each ask is described from its stored request after the DM attempt, so the ack
never says someone was asked unless their DM was recorded as sent. Pure text
work: deterministic, no model, people named, never by a chat id.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.application.person_names import CHAT_ID

# Who an unmatched ask named, when the name itself is a chat id or a mention.
_SOMEONE_NAMED = "the person you named"
_TELL_WHEN_ANSWERED = "I'll tell you when they answer."


class AskRouting(StrEnum):
    """What became of one ask by the time the ack goes out."""

    #: The counterpart's DM went out.
    ASKED = "asked"
    #: A repeat of an open request of theirs: refreshed, nobody DMed again.
    ALREADY_OPEN = "already_open"
    #: No member goes by the name the ask gave.
    NOT_FOUND = "not_found"
    #: Several members do, and nobody said which.
    AMBIGUOUS = "ambiguous"
    #: A member with no chat id, whose DM did not go out.
    NO_CHAT_ID = "no_chat_id"
    #: The DM failed and another attempt is due.
    NOT_REACHED = "not_reached"
    #: Nothing was sent: notification is off, or every attempt failed.
    NOT_SENT = "not_sent"


@dataclass(frozen=True, kw_only=True)
class AskAck:
    """One ask as the ack names it."""

    routing: AskRouting
    #: review, input or dependency.
    kind: str
    #: The person asked, by name; for an unmatched ask, the name it gave.
    name: str
    #: The work the request names, as it names it ("storefront-web !2").
    reference: str | None = None
    #: The day the open request was made, for ``ALREADY_OPEN``.
    since: date | None = None


def asks_note(asks: Sequence[AskAck], max_chars: int) -> str | None:
    """The asks part of the ack, within ``max_chars``; None when there is nothing to say.

    "I've asked Noah Weber to review storefront-web !2 and will tell you when
    they answer." Several asks are listed in one sentence. When the full note
    does not fit, it names only the people; when that does not fit either, it
    is left out, so the ack never says less than the truth about someone.
    """
    if not asks:
        return None
    for note in (_full_note(asks), _short_note(asks)):
        if len(note) <= max_chars:
            return note
    return None


def _full_note(asks: Sequence[AskAck]) -> str:
    asked = [ask for ask in asks if ask.routing is AskRouting.ASKED]
    already = [ask for ask in asks if ask.routing is AskRouting.ALREADY_OPEN]
    pending: list[str] = []
    if asked:
        pending.append(
            "I've asked " + _join([f"{ask.name} {_ask_for(ask)}" for ask in asked], "and")
        )
    pending.extend(
        f"{ask.name} already has your open {_open_request(ask)}{_since(ask)}" for ask in already
    )
    sentences: list[str] = []
    if len(pending) == 1 and asked:
        joiner = ", and" if len(asked) > 1 else " and"
        sentences.append(f"{pending[0]}{joiner} will tell you when they answer.")
    elif pending:
        sentences.append(f"{', and '.join(pending)}; {_TELL_WHEN_ANSWERED}")
    sentences.extend(_unrouted(ask) for ask in asks if ask.routing not in _PENDING)
    return " ".join(sentences)


def _short_note(asks: Sequence[AskAck]) -> str:
    """Only who: "I've asked Noah Weber and Liam Chen; I'll tell you when they answer."""
    asked = _names(asks, AskRouting.ASKED)
    already = _names(asks, AskRouting.ALREADY_OPEN)
    unrouted = list(dict.fromkeys(ask.name for ask in asks if ask.routing not in _PENDING))
    pending: list[str] = []
    if asked:
        pending.append(f"I've asked {_join(asked, 'and')}")
    if already:
        has = "already have open requests" if len(already) > 1 else "already has an open request"
        pending.append(f"{_join(already, 'and')} {has} from you")
    sentences = [f"{', and '.join(pending)}; {_TELL_WHEN_ANSWERED}"] if pending else []
    if unrouted:
        sentences.append(f"I haven't asked {_join(unrouted, 'or')}.")
    return " ".join(sentences)


def _unrouted(ask: AskAck) -> str:
    what = _ask_for(ask)
    if ask.routing is AskRouting.NOT_FOUND:
        return f"I couldn't find {ask.name} on the team, so I haven't asked anyone {what}."
    if ask.routing is AskRouting.AMBIGUOUS:
        return f"I couldn't tell which {ask.name} you meant, so I haven't asked anyone {what}."
    if ask.routing is AskRouting.NO_CHAT_ID:
        return f"{ask.name} has no chat id yet, so I couldn't ask them {what}."
    if ask.routing is AskRouting.NOT_REACHED:
        return f"I couldn't reach {ask.name} in chat to ask them {what}; I'll try again."
    return f"I recorded your ask of {ask.name} {what} but haven't messaged them."


def _ask_for(ask: AskAck) -> str:
    """What the person is asked for: "to review CHK-8", "for input", "about IDP-6"."""
    ref = ask.reference
    if ask.kind == "review":
        return f"to review {ref}" if ref else "for a review"
    if ask.kind == "input":
        return f"for input on {ref}" if ref else "for input"
    return f"about {ref}" if ref else "about what you're waiting on"


def _open_request(ask: AskAck) -> str:
    ref = ask.reference
    if ask.kind == "review":
        return f"review request for {ref}" if ref else "review request"
    if ask.kind == "input":
        return f"input request on {ref}" if ref else "input request"
    return f"request about {ref}" if ref else "request"


def _since(ask: AskAck) -> str:
    return f" from {ask.since.day} {ask.since:%b}" if ask.since is not None else ""


def _names(asks: Sequence[AskAck], routing: AskRouting) -> list[str]:
    return list(dict.fromkeys(ask.name for ask in asks if ask.routing is routing))


def _join(items: Sequence[str], word: str) -> str:
    if len(items) <= 2:
        return f" {word} ".join(items)
    return f"{', '.join(items[:-1])} {word} {items[-1]}"


def named_as_given(raw_name: str) -> str:
    """The name an unmatched ask gave, unless it is a chat id or a mention."""
    name = " ".join(raw_name.split())
    if not name or name.startswith("@") or CHAT_ID.search(name):
        return _SOMEONE_NAMED
    return name


_PENDING = frozenset({AskRouting.ASKED, AskRouting.ALREADY_OPEN})
