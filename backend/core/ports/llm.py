from __future__ import annotations

from typing import Protocol

from core.domain.llm import LlmRequest, LlmResponse


class LlmProvider(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...
