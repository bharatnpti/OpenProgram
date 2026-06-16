from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage


class ChatProvider(Protocol):
    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str: ...

    async def open_thread(self, user: ChatUserRef) -> str: ...

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None: ...


class ChatWebhookMapper(Protocol):
    def map_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None: ...
