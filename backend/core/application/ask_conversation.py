"""Ask's conversation: what was said before the question, kept short enough to send.

The console keeps the conversation and sends it with each question: the turns
since the last summary, and that summary. The model reads them before the
question, so "and Identity?" or "who owns that?" means what the person meant.

A long conversation is compacted: past ``COMPACT_AT_TURNS`` turns or
``COMPACT_AT_CHARS`` characters, every turn but the last ``KEEP_TURNS`` is
folded, with the summary so far, into a new summary of at most
``SUMMARY_WORDS`` words. The reply says how many turns the summary now covers,
so the console sends only the newer ones next time. The recent turns always
go word for word; the summary keeps what they rest on.

Earlier answers are context, not facts: the prompts tell the model to look
facts up again with the tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import structlog

from core.domain.llm import LlmMessage, LlmRequest
from core.ports.llm import LlmProvider

_logger = structlog.get_logger(__name__)

# What a request may carry: past these the request is refused (422).
MAX_TURNS = 40
MAX_TURN_CHARS = 4000
MAX_SUMMARY_CHARS = 2000
# When the turns are folded into the summary, and how many stay as they are.
COMPACT_AT_TURNS = 12
COMPACT_AT_CHARS = 8000
KEEP_TURNS = 6
SUMMARY_WORDS = 150

SUMMARY_SYSTEM_PROMPT = (
    "You keep the memory of a conversation between a program-management assistant and "
    "the person asking it. Write what the conversation so far established: what was "
    "asked, the facts the answers gave -- names, counts, dates, colours -- and what is "
    f"still open. Keep names as written and leave out ids. {SUMMARY_WORDS} words at most. "
    "Reply with the summary text and nothing else."
)

type TurnRole = Literal["user", "assistant"]


@dataclass(frozen=True, kw_only=True)
class AskTurn:
    role: TurnRole
    content: str


@dataclass(frozen=True, kw_only=True)
class AskConversation:
    """What the console sends: the summary so far and the turns since it."""

    summary: str | None = None
    turns: tuple[AskTurn, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AskContext:
    """What the model is given before the question, and what the console should keep.

    ``summarized`` is how many of the turns sent are now covered by
    ``summary``: the console drops that many of its oldest turns and keeps
    ``summary`` in their place. Zero when nothing was compacted.
    """

    summary: str | None
    turns: tuple[AskTurn, ...]
    summarized: int = 0

    def messages(self) -> tuple[LlmMessage, ...]:
        summary = (
            (
                LlmMessage(
                    role="user",
                    content=f"Earlier in this conversation, in short: {self.summary}",
                ),
            )
            if self.summary
            else ()
        )
        return (*summary, *(LlmMessage(role=t.role, content=t.content) for t in self.turns))


async def context_for(
    conversation: AskConversation | None,
    *,
    llm: LlmProvider,
    model: str,
    tenant_id: str,
    correlation_id: str,
) -> AskContext:
    """The conversation as the model reads it, compacted first when it has grown long."""
    if conversation is None:
        return AskContext(summary=None, turns=())
    turns = _recent_first_user(conversation.turns)
    if not _too_long(turns):
        return AskContext(summary=conversation.summary, turns=turns)
    older, recent = turns[:-KEEP_TURNS], turns[-KEEP_TURNS:]
    recent = _recent_first_user(recent)
    # Counted against what the console sent, so it drops exactly these.
    folded = len(conversation.turns) - len(recent)
    request = LlmRequest(
        tenant_id=tenant_id,
        prompt=_transcript(conversation.summary, older),
        model=model,
        correlation_id=correlation_id,
        system=SUMMARY_SYSTEM_PROMPT,
        metadata={"agent": "ask_service", "purpose": "conversation_summary"},
    )
    try:
        response = await llm.complete(request)
    # Any failure: the question is still answered, from the recent turns and
    # the summary the console had.
    except Exception as exc:
        _logger.warning(
            "ask_conversation_summary_failed",
            correlation_id=correlation_id,
            error_type=type(exc).__name__,
        )
        return AskContext(summary=conversation.summary, turns=recent, summarized=folded)
    summary = response.text.strip()[:MAX_SUMMARY_CHARS] or conversation.summary
    return AskContext(summary=summary, turns=recent, summarized=folded)


def _too_long(turns: Sequence[AskTurn]) -> bool:
    return len(turns) > COMPACT_AT_TURNS or sum(len(t.content) for t in turns) > COMPACT_AT_CHARS


def _recent_first_user(turns: Sequence[AskTurn]) -> tuple[AskTurn, ...]:
    """The turns from the first question on: an answer whose question is gone reads alone."""
    first = next((index for index, turn in enumerate(turns) if turn.role == "user"), len(turns))
    return tuple(turns[first:])


def _transcript(summary: str | None, turns: Sequence[AskTurn]) -> str:
    said = "\n".join(
        f"{'Asker' if turn.role == 'user' else 'Assistant'}: {turn.content}" for turn in turns
    )
    before = f"The summary so far: {summary}\n" if summary else ""
    return f"{before}The conversation since:\n{said}"
