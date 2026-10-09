"""Guardrails every real language-model call passes before it is sent.

Two checks, both made before any tokens are spent:

- a request carrying more text than ``max_request_chars`` is refused. No
  check-in, question or brief comes near it; a pasted file or a loop that
  keeps growing its context does.
- once a tenant has spent ``daily_token_budget`` tokens in a UTC day, further
  calls are refused until the next day. A runaway loop stops there instead of
  running up the bill; normal use stays far below it.

A refusal raises an :class:`~core.domain.errors.LlmCallRefused`, a
``ProviderUnavailable``, so every caller degrades exactly as it does when the
model endpoint is down: briefs and gate scans fall back, a reply waits in the
inbound queue and is processed once calls are allowed again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol

import structlog
from redis.asyncio import Redis

from core.domain.errors import LlmBudgetExhausted, LlmRequestTooLarge
from core.domain.llm import LlmRequest, LlmResponse

_logger = structlog.get_logger(__name__)

# A day's count outlives its day a little, so a late add never resets it.
_LEDGER_TTL_SECONDS = 2 * 86_400


class TokenLedger(Protocol):
    """Tokens a tenant has spent per UTC day."""

    async def used(self, tenant_id: str, day: date) -> int: ...

    async def add(self, tenant_id: str, day: date, tokens: int) -> None: ...


@dataclass
class InMemoryTokenLedger:
    _spent: dict[tuple[str, date], int] = field(default_factory=dict)

    async def used(self, tenant_id: str, day: date) -> int:
        return self._spent.get((tenant_id, day), 0)

    async def add(self, tenant_id: str, day: date, tokens: int) -> None:
        self._spent[(tenant_id, day)] = self._spent.get((tenant_id, day), 0) + tokens


@dataclass(frozen=True)
class RedisTokenLedger:
    client: Redis

    async def used(self, tenant_id: str, day: date) -> int:
        value = await self.client.get(self._key(tenant_id, day))
        return int(value) if value else 0

    async def add(self, tenant_id: str, day: date, tokens: int) -> None:
        key = self._key(tenant_id, day)
        await self.client.incrby(key, tokens)
        await self.client.expire(key, _LEDGER_TTL_SECONDS)

    def _key(self, tenant_id: str, day: date) -> str:
        return f"openprogram:llm:tokens:{tenant_id}:{day.isoformat()}"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class LlmCallGuard:
    ledger: TokenLedger
    #: Tokens per tenant per UTC day; 0 is no budget.
    daily_token_budget: int
    #: Characters of text one request may carry; 0 is no ceiling.
    max_request_chars: int
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def admit(self, request: LlmRequest) -> None:
        """Raise when the request must not be sent; return when it may."""
        size = request_chars(request)
        if self.max_request_chars and size > self.max_request_chars:
            _logger.warning(
                "llm_call_refused",
                guardrail="max_request_chars",
                tenant_id=request.tenant_id,
                correlation_id=request.correlation_id,
                purpose=request.metadata.get("purpose"),
                request_chars=size,
                limit=self.max_request_chars,
            )
            msg = f"the request carries {size} characters, more than {self.max_request_chars}"
            raise LlmRequestTooLarge(msg)
        if not self.daily_token_budget:
            return
        used = await self.ledger.used(request.tenant_id, self._today())
        if used >= self.daily_token_budget:
            _logger.error(
                "llm_call_refused",
                guardrail="daily_token_budget",
                tenant_id=request.tenant_id,
                correlation_id=request.correlation_id,
                purpose=request.metadata.get("purpose"),
                tokens_used=used,
                limit=self.daily_token_budget,
            )
            msg = "today's language-model token budget is used up"
            raise LlmBudgetExhausted(msg)

    async def record(self, request: LlmRequest, response: LlmResponse) -> None:
        """Count a completed call's tokens. A ledger failure never loses the response."""
        if not self.daily_token_budget or response.usage.total_tokens <= 0:
            return
        try:
            await self.ledger.add(request.tenant_id, self._today(), response.usage.total_tokens)
        except Exception as exc:  # noqa: BLE001 - accounting is best effort once paid for
            _logger.warning(
                "llm_token_ledger_add_failed",
                tenant_id=request.tenant_id,
                error_type=type(exc).__name__,
            )

    def _today(self) -> date:
        return self.clock().astimezone(UTC).date()


def request_chars(request: LlmRequest) -> int:
    """The characters of text the model is asked to read."""
    return (
        len(request.system or "")
        + len(request.instructions or "")
        + len(request.prompt)
        + sum(len(message.content) for message in request.messages)
        + sum(len(result.content) for result in request.tool_results)
        + sum(len(turn.content) for turn in request.turns)
    )
