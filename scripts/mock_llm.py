from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

app = FastAPI(title="OpenProgram Mock LLM")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    prompt = ""
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict) and isinstance(last.get("content"), str):
            prompt = str(last["content"])
    summary = f"Mock status summary: {prompt[:160]}"
    prompt_tokens = max(1, len(prompt.split()))
    completion_tokens = max(1, len(summary.split()))
    return {
        "id": f"chatcmpl-{uuid4()}",
        "object": "chat.completion",
        "created": int(time()),
        "model": payload.get("model", "local-gpt"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": summary},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
