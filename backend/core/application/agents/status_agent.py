from __future__ import annotations

from collections.abc import Mapping

from core.domain.llm import LlmRequest, LlmResponse
from core.ports.llm import LlmProvider


class StatusAgentNode:
    def __init__(self, llm_provider: LlmProvider, model: str) -> None:
        self._llm_provider = llm_provider
        self._model = model

    async def summarize_status(
        self,
        *,
        tenant_id: str,
        developer_name: str,
        context: str,
        correlation_id: str,
    ) -> LlmResponse:
        prompt = (
            "Summarize this developer status for a program manager. "
            "Return concise progress, blocker, and risk bullets. "
            f"Developer: {developer_name}. Context: {context}"
        )
        return await self._llm_provider.complete(
            LlmRequest(
                tenant_id=tenant_id,
                prompt=prompt,
                model=self._model,
                correlation_id=correlation_id,
                metadata={"agent": "status_agent"},
            )
        )

    async def __call__(self, state: Mapping[str, str]) -> dict[str, str]:
        response = await self.summarize_status(
            tenant_id=state["tenant_id"],
            developer_name=state["developer_name"],
            context=state["context"],
            correlation_id=state["correlation_id"],
        )
        return {"summary": response.text, "trace_id": response.trace_id}
