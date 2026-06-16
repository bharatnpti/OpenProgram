from __future__ import annotations

import asyncio
from typing import NoReturn

import httpx

from config.settings import get_settings
from core.application.agents.status_agent import StatusAgentNode
from infra.registry import ServiceRegistry


async def main() -> None:
    settings = get_settings()
    registry = ServiceRegistry(settings)
    readiness = await registry.readiness()
    failed = [name for name, ok in readiness.items() if not ok]
    if failed:
        _die(f"readiness checks failed: {', '.join(failed)}")

    agent = StatusAgentNode(registry.llm_provider(), model=settings.litellm_model)
    result = await agent.graph().ainvoke(
        {
            "tenant_id": settings.tenant_id,
            "developer_name": "Asha",
            "context": "Container-backed Phase 0 smoke test.",
            "correlation_id": "smoke-phase-0",
        }
    )
    trace_id = str(result["trace_id"])
    await _wait_for_langfuse_trace(
        settings.langfuse_host,
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        trace_id,
    )
    print(f"smoke ok: trace {trace_id}")


async def _wait_for_langfuse_trace(
    host: str,
    public_key: str | None,
    secret_key: str | None,
    trace_id: str,
) -> None:
    if not public_key or not secret_key:
        _die("langfuse keys are required for smoke trace verification")
    async with httpx.AsyncClient(
        base_url=host,
        auth=(public_key, secret_key),
        timeout=5.0,
    ) as client:
        for _ in range(30):
            response = await client.get(f"/api/public/traces/{trace_id}")
            if response.status_code == 200:
                return
            await asyncio.sleep(1.0)
    _die(f"langfuse trace was not visible: {trace_id}")


def _die(message: str) -> NoReturn:
    raise SystemExit(message)


if __name__ == "__main__":
    asyncio.run(main())
