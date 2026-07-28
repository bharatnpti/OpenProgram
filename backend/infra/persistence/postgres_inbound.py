from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.inbound import InboundChatEvent

_tracer = trace.get_tracer("openprogram.persistence.inbound")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresInboundChatEventRepository:
    """Durable buffer for inbound chat events backing fast-ack processing.

    Raw DM content in ``text``/``raw_payload`` never leaves this table via logs,
    traces, persona views, or public APIs; processed rows are purged on the
    conversation retention path.
    """

    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def append(self, event: InboundChatEvent) -> bool:
        with _tracer.start_as_current_span("postgres.inbound.append"):
            rows = await self._executor.fetch(
                """
                INSERT INTO inbound_chat_events (
                    tenant_id, provider, event_id, conversation_key, chat_user_ref,
                    chat_thread_ref, message_ref, outbound_message_id, correlation_id,
                    text, raw_payload, received_at, attempts
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, provider, event_id) DO NOTHING
                RETURNING id::text AS inserted_id
                """,
                (
                    event.tenant_id,
                    event.provider,
                    event.event_id,
                    event.conversation_key,
                    event.chat_user_ref,
                    event.chat_thread_ref,
                    event.message_ref,
                    event.outbound_message_id,
                    event.correlation_id,
                    event.text,
                    _payload_dict(event.raw_payload),
                    event.received_at,
                    event.attempts,
                ),
            )
        return bool(rows)

    async def list_unprocessed_for_conversation(
        self, tenant_id: str, conversation_key: str
    ) -> list[InboundChatEvent]:
        with _tracer.start_as_current_span("postgres.inbound.list_unprocessed"):
            rows = await self._executor.fetch(
                """
                SELECT id::text AS row_id, tenant_id, provider, event_id, conversation_key,
                       chat_user_ref, chat_thread_ref, message_ref, outbound_message_id,
                       correlation_id, text, raw_payload, received_at, processed_at, attempts
                FROM inbound_chat_events
                WHERE tenant_id = %s
                  AND conversation_key = %s
                  AND processed_at IS NULL
                ORDER BY received_at ASC, message_ref ASC
                """,
                (tenant_id, conversation_key),
            )
        return [_event_from_row(row) for row in rows]

    async def mark_processed(
        self, tenant_id: str, event_ids: Sequence[str], processed_at: datetime
    ) -> None:
        if not event_ids:
            return
        with _tracer.start_as_current_span("postgres.inbound.mark_processed"):
            await self._executor.execute(
                """
                UPDATE inbound_chat_events
                SET processed_at = %s, attempts = attempts + 1
                WHERE tenant_id = %s
                  AND processed_at IS NULL
                  AND id::text = ANY(%s)
                """,
                (processed_at, tenant_id, list(event_ids)),
            )

    async def list_stuck(self, tenant_id: str, older_than: datetime) -> list[InboundChatEvent]:
        with _tracer.start_as_current_span("postgres.inbound.list_stuck"):
            rows = await self._executor.fetch(
                """
                SELECT id::text AS row_id, tenant_id, provider, event_id, conversation_key,
                       chat_user_ref, chat_thread_ref, message_ref, outbound_message_id,
                       correlation_id, text, raw_payload, received_at, processed_at, attempts
                FROM inbound_chat_events
                WHERE tenant_id = %s
                  AND processed_at IS NULL
                  AND received_at < %s
                ORDER BY received_at ASC, message_ref ASC
                """,
                (tenant_id, older_than),
            )
        return [_event_from_row(row) for row in rows]

    async def purge_processed_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        with _tracer.start_as_current_span("postgres.inbound.purge"):
            rows = await self._executor.fetch(
                """
                WITH deleted AS (
                    DELETE FROM inbound_chat_events
                    WHERE tenant_id = %s
                      AND processed_at IS NOT NULL
                      AND processed_at < %s
                    RETURNING 1
                )
                SELECT count(*) AS deleted_count FROM deleted
                """,
                (tenant_id, cutoff),
            )
        return _as_int(rows[0]["deleted_count"]) if rows else 0


def _payload_dict(raw_payload: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_from_row(row: Mapping[str, object]) -> InboundChatEvent:
    raw_payload = row.get("raw_payload")
    if isinstance(raw_payload, str):
        raw_payload_text = raw_payload
    elif isinstance(raw_payload, dict):
        raw_payload_text = json.dumps(raw_payload)
    else:
        raw_payload_text = "{}"
    return InboundChatEvent(
        id=str(row["row_id"]),
        tenant_id=str(row["tenant_id"]),
        provider=str(row["provider"]),
        event_id=str(row["event_id"]),
        conversation_key=str(row["conversation_key"]),
        chat_user_ref=str(row["chat_user_ref"]),
        chat_thread_ref=_optional_str(row.get("chat_thread_ref")),
        message_ref=str(row["message_ref"]),
        outbound_message_id=_optional_str(row.get("outbound_message_id")),
        correlation_id=str(row["correlation_id"]),
        text=str(row["text"]),
        raw_payload=raw_payload_text,
        received_at=_datetime(row["received_at"]),
        processed_at=_optional_datetime(row.get("processed_at")),
        attempts=_as_int(row.get("attempts")),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError("inbound_chat_events row missing received_at timestamp")


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None
