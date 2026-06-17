from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ConversationRole(StrEnum):
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


@dataclass(frozen=True, kw_only=True)
class ConversationTurn:
    tenant_id: str
    developer_id: str
    conversation_id: str
    conversation_date: date
    role: ConversationRole
    content: str
    correlation_id: str | None
    chat_message_id: str | None
    observed_at: datetime
