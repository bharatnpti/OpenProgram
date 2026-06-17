from __future__ import annotations

from collections.abc import Iterable

from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.llm import LlmMessage, LlmMessageRole


def llm_messages_from_turns(turns: Iterable[ConversationTurn]) -> tuple[LlmMessage, ...]:
    return tuple(
        LlmMessage(role=_llm_role_for_turn(turn.role), content=turn.content) for turn in turns
    )


def _llm_role_for_turn(role: ConversationRole) -> LlmMessageRole:
    if role is ConversationRole.AGENT:
        return "assistant"
    if role is ConversationRole.USER:
        return "user"
    return "system"
