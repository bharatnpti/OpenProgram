from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.domain.graph import JsonScalar


@dataclass(frozen=True, kw_only=True)
class ChatUserRef:
    tenant_id: str
    external_id: str
    display_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class OutboundMessage:
    tenant_id: str
    text: str
    correlation_id: str
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class InboundMessage:
    tenant_id: str
    user: ChatUserRef
    text: str
    thread_id: str
    message_id: str
    correlation_id: str
    received_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
