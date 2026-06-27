from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Protocol

from opentelemetry import trace

from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.graph import EntityRef, JsonScalar, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    Mood,
    StatusSource,
)

_tracer = trace.get_tracer("pulseops.persistence.status")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresStatusRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def record_checkin(self, checkin: CheckIn) -> None:
        with _tracer.start_as_current_span("postgres.status.record_checkin"):
            await self._executor.execute(
                """
                INSERT INTO checkins (
                    tenant_id, developer_id, correlation_id, asked_at,
                    replied_at, raw_reply, signals
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, correlation_id)
                DO UPDATE SET
                    developer_id = EXCLUDED.developer_id,
                    asked_at = EXCLUDED.asked_at,
                    replied_at = EXCLUDED.replied_at,
                    raw_reply = EXCLUDED.raw_reply,
                    signals = EXCLUDED.signals
                """,
                (
                    checkin.tenant_id,
                    checkin.developer_id,
                    checkin.correlation_id,
                    checkin.asked_at,
                    checkin.replied_at,
                    checkin.raw_reply,
                    _signals_to_json(checkin.signals),
                ),
            )

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        with _tracer.start_as_current_span("postgres.status.checkin_by_correlation"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, correlation_id, asked_at,
                       replied_at, raw_reply, signals
                FROM checkins
                WHERE tenant_id = %s AND correlation_id = %s
                LIMIT 1
                """,
                (tenant_id, correlation_id),
            )
        return _checkin_from_row(rows[0]) if rows else None

    async def record_checkin_correlation(self, correlation: CheckInCorrelation) -> None:
        with _tracer.start_as_current_span("postgres.status.record_checkin_correlation"):
            await self._executor.execute(
                """
                INSERT INTO checkin_correlations (
                    tenant_id, correlation_id, developer_id, chat_user_ref, chat_thread_ref,
                    outbound_message_id, asked_at, consumed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, correlation_id)
                DO UPDATE SET
                    developer_id = EXCLUDED.developer_id,
                    chat_user_ref = EXCLUDED.chat_user_ref,
                    chat_thread_ref = EXCLUDED.chat_thread_ref,
                    outbound_message_id = EXCLUDED.outbound_message_id,
                    asked_at = EXCLUDED.asked_at,
                    consumed_at = COALESCE(
                        checkin_correlations.consumed_at,
                        EXCLUDED.consumed_at
                    )
                """,
                (
                    correlation.tenant_id,
                    correlation.correlation_id,
                    correlation.developer_id,
                    correlation.chat_user_ref,
                    correlation.chat_thread_ref,
                    correlation.outbound_message_id,
                    correlation.asked_at,
                    correlation.consumed_at,
                ),
            )

    async def checkin_correlation_by_id(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInCorrelation | None:
        with _tracer.start_as_current_span("postgres.status.checkin_correlation_by_id"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, correlation_id, developer_id, chat_user_ref, chat_thread_ref,
                       outbound_message_id, asked_at, consumed_at
                FROM checkin_correlations
                WHERE tenant_id = %s AND correlation_id = %s
                LIMIT 1
                """,
                (tenant_id, correlation_id),
            )
        return _checkin_correlation_from_row(rows[0]) if rows else None

    async def latest_checkin_correlation_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        with _tracer.start_as_current_span("postgres.status.latest_checkin_correlation_for_thread"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, correlation_id, developer_id, chat_user_ref, chat_thread_ref,
                       outbound_message_id, asked_at, consumed_at
                FROM checkin_correlations
                WHERE tenant_id = %s
                  AND chat_thread_ref = %s
                  AND asked_at >= %s::date
                  AND asked_at < (%s::date + INTERVAL '1 day')
                ORDER BY asked_at DESC
                LIMIT 1
                """,
                (tenant_id, chat_thread_ref, as_of, as_of),
            )
        return _checkin_correlation_from_row(rows[0]) if rows else None

    async def unconsumed_checkin_correlations_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        with _tracer.start_as_current_span(
            "postgres.status.unconsumed_checkin_correlations_for_thread"
        ):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, correlation_id, developer_id, chat_user_ref, chat_thread_ref,
                       outbound_message_id, asked_at, consumed_at
                FROM checkin_correlations
                WHERE tenant_id = %s
                  AND chat_thread_ref = %s
                  AND consumed_at IS NULL
                  AND asked_at >= %s::date
                  AND asked_at < (%s::date + INTERVAL '1 day')
                ORDER BY asked_at DESC
                """,
                (tenant_id, chat_thread_ref, as_of, as_of),
            )
        return [_checkin_correlation_from_row(row) for row in rows]

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        correlations = await self.unconsumed_checkin_correlations_for_user(
            tenant_id,
            chat_user_ref,
            as_of,
        )
        return correlations[0] if correlations else None

    async def unconsumed_checkin_correlations_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        with _tracer.start_as_current_span(
            "postgres.status.unconsumed_checkin_correlations_for_user"
        ):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, correlation_id, developer_id, chat_user_ref, chat_thread_ref,
                       outbound_message_id, asked_at, consumed_at
                FROM checkin_correlations
                WHERE tenant_id = %s
                  AND chat_user_ref = %s
                  AND consumed_at IS NULL
                  AND asked_at >= %s::date
                  AND asked_at < (%s::date + INTERVAL '1 day')
                ORDER BY asked_at DESC
                """,
                (tenant_id, chat_user_ref, as_of, as_of),
            )
        return [_checkin_correlation_from_row(row) for row in rows]

    async def consume_checkin_correlation(
        self, tenant_id: str, correlation_id: str, consumed_at: datetime
    ) -> None:
        with _tracer.start_as_current_span("postgres.status.consume_checkin_correlation"):
            await self._executor.execute(
                """
                UPDATE checkin_correlations
                SET consumed_at = COALESCE(consumed_at, %s)
                WHERE tenant_id = %s AND correlation_id = %s
                """,
                (consumed_at, tenant_id, correlation_id),
            )

    async def record_checkin_preference(self, preference: CheckInPreference) -> None:
        with _tracer.start_as_current_span("postgres.status.record_checkin_preference"):
            await self._executor.execute(
                """
                INSERT INTO checkin_preferences (
                    tenant_id, developer_id, local_time, timezone, weekdays,
                    reply_wait_seconds, final_reply_wait_seconds
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, developer_id)
                DO UPDATE SET
                    local_time = EXCLUDED.local_time,
                    timezone = EXCLUDED.timezone,
                    weekdays = EXCLUDED.weekdays,
                    reply_wait_seconds = EXCLUDED.reply_wait_seconds,
                    final_reply_wait_seconds = EXCLUDED.final_reply_wait_seconds,
                    updated_at = now()
                """,
                (
                    preference.tenant_id,
                    preference.developer_id,
                    preference.local_time,
                    preference.timezone,
                    _int_tuple_to_json(preference.weekdays),
                    preference.reply_wait_seconds,
                    preference.final_reply_wait_seconds,
                ),
            )

    async def checkin_preference_for(
        self, tenant_id: str, developer_id: str
    ) -> CheckInPreference | None:
        with _tracer.start_as_current_span("postgres.status.checkin_preference_for"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, local_time, timezone, weekdays,
                       reply_wait_seconds, final_reply_wait_seconds
                FROM checkin_preferences
                WHERE tenant_id = %s AND developer_id = %s
                LIMIT 1
                """,
                (tenant_id, developer_id),
            )
        return _checkin_preference_from_row(rows[0]) if rows else None

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]:
        with _tracer.start_as_current_span("postgres.status.list_checkin_preferences"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, local_time, timezone, weekdays,
                       reply_wait_seconds, final_reply_wait_seconds
                FROM checkin_preferences
                WHERE tenant_id = %s
                ORDER BY developer_id
                """,
                (tenant_id,),
            )
        return [_checkin_preference_from_row(row) for row in rows]

    async def delete_checkin_preference(self, tenant_id: str, developer_id: str) -> None:
        with _tracer.start_as_current_span("postgres.status.delete_checkin_preference"):
            await self._executor.execute(
                """
                DELETE FROM checkin_preferences
                WHERE tenant_id = %s AND developer_id = %s
                """,
                (tenant_id, developer_id),
            )

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        with _tracer.start_as_current_span("postgres.status.record_checkin_schedule_run"):
            await self._executor.execute(
                """
                INSERT INTO checkin_schedule_runs (
                    tenant_id, developer_id, checkin_date, correlation_id, status,
                    scheduled_at, reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, developer_id, checkin_date)
                DO UPDATE SET
                    correlation_id = EXCLUDED.correlation_id,
                    status = EXCLUDED.status,
                    scheduled_at = EXCLUDED.scheduled_at,
                    reason = EXCLUDED.reason,
                    updated_at = now()
                """,
                (
                    run.tenant_id,
                    run.developer_id,
                    run.checkin_date,
                    run.correlation_id,
                    run.status,
                    run.scheduled_at,
                    run.reason,
                ),
            )

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        with _tracer.start_as_current_span("postgres.status.checkin_schedule_run"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, checkin_date, correlation_id, status,
                       scheduled_at, reason
                FROM checkin_schedule_runs
                WHERE tenant_id = %s AND developer_id = %s AND checkin_date = %s
                LIMIT 1
                """,
                (tenant_id, developer_id, checkin_date),
            )
        return _checkin_schedule_run_from_row(rows[0]) if rows else None

    async def record_checkin_nudge(self, nudge: CheckInNudge) -> CheckInNudge:
        with _tracer.start_as_current_span("postgres.status.record_checkin_nudge"):
            rows = await self._executor.fetch(
                """
                INSERT INTO checkin_nudges (
                    tenant_id, correlation_id, nudge_number, sent_at, outbound_message_id
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, correlation_id, nudge_number)
                DO UPDATE SET
                    sent_at = COALESCE(checkin_nudges.sent_at, EXCLUDED.sent_at),
                    outbound_message_id = COALESCE(
                        checkin_nudges.outbound_message_id,
                        EXCLUDED.outbound_message_id
                    )
                RETURNING tenant_id, correlation_id, nudge_number, sent_at, outbound_message_id
                """,
                (
                    nudge.tenant_id,
                    nudge.correlation_id,
                    nudge.nudge_number,
                    nudge.sent_at,
                    nudge.outbound_message_id,
                ),
            )
        return _checkin_nudge_from_row(rows[0])

    async def checkin_nudge_for(
        self, tenant_id: str, correlation_id: str, nudge_number: int
    ) -> CheckInNudge | None:
        with _tracer.start_as_current_span("postgres.status.checkin_nudge_for"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, correlation_id, nudge_number, sent_at, outbound_message_id
                FROM checkin_nudges
                WHERE tenant_id = %s AND correlation_id = %s AND nudge_number = %s
                LIMIT 1
                """,
                (tenant_id, correlation_id, nudge_number),
            )
        return _checkin_nudge_from_row(rows[0]) if rows else None

    async def record_checkin_clarification(
        self, clarification: CheckInClarification
    ) -> CheckInClarification:
        with _tracer.start_as_current_span("postgres.status.record_checkin_clarification"):
            rows = await self._executor.fetch(
                """
                INSERT INTO checkin_clarifications (
                    tenant_id, correlation_id, clarification_number, question,
                    sent_at, outbound_message_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, correlation_id, clarification_number)
                DO UPDATE SET
                    sent_at = COALESCE(checkin_clarifications.sent_at, EXCLUDED.sent_at),
                    outbound_message_id = COALESCE(
                        checkin_clarifications.outbound_message_id,
                        EXCLUDED.outbound_message_id
                    )
                RETURNING tenant_id, correlation_id, clarification_number, question,
                          sent_at, outbound_message_id
                """,
                (
                    clarification.tenant_id,
                    clarification.correlation_id,
                    clarification.clarification_number,
                    clarification.question,
                    clarification.sent_at,
                    clarification.outbound_message_id,
                ),
            )
        return _checkin_clarification_from_row(rows[0])

    async def checkin_clarification_count(self, tenant_id: str, correlation_id: str) -> int:
        with _tracer.start_as_current_span("postgres.status.checkin_clarification_count"):
            rows = await self._executor.fetch(
                """
                SELECT count(*) AS clarification_count
                FROM checkin_clarifications
                WHERE tenant_id = %s AND correlation_id = %s
                """,
                (tenant_id, correlation_id),
            )
        return _int_field(rows[0]["clarification_count"], "clarification_count") if rows else 0

    async def record_developer_status(self, status: DeveloperStatus) -> None:
        with _tracer.start_as_current_span("postgres.status.record_developer_status"):
            await self._executor.execute(
                """
                INSERT INTO developer_statuses (
                    tenant_id, developer_id, as_of, source, blockers, summary,
                    eta_change_days, mood
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, developer_id, as_of)
                DO UPDATE SET
                    source = EXCLUDED.source,
                    blockers = EXCLUDED.blockers,
                    summary = EXCLUDED.summary,
                    eta_change_days = EXCLUDED.eta_change_days,
                    mood = EXCLUDED.mood,
                    updated_at = now()
                """,
                (
                    status.tenant_id,
                    status.developer_id,
                    status.as_of,
                    status.source.value,
                    _string_tuple_to_json(status.blockers),
                    status.summary,
                    status.eta_change_days,
                    status.mood.value if status.mood else None,
                ),
            )

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        with _tracer.start_as_current_span("postgres.status.latest_developer_status"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, as_of, source, blockers, summary,
                       eta_change_days, mood
                FROM developer_statuses
                WHERE tenant_id = %s AND developer_id = %s AND as_of <= %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (tenant_id, developer_id, as_of),
            )
        return _developer_status_from_row(rows[0]) if rows else None

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]:
        with _tracer.start_as_current_span("postgres.status.developers_without_checkin"):
            rows = await self._executor.fetch(
                """
                WITH known AS (
                    SELECT id AS developer_id
                    FROM graph_nodes
                    WHERE tenant_id = %s AND kind = 'developer'
                  UNION
                    SELECT developer_id
                    FROM developer_statuses
                    WHERE tenant_id = %s
                  UNION
                    SELECT developer_id
                    FROM checkins
                    WHERE tenant_id = %s
                )
                SELECT known.developer_id
                FROM known
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM checkins replied
                    WHERE replied.tenant_id = %s
                      AND replied.developer_id = known.developer_id
                      AND replied.replied_at >= %s::date
                      AND replied.replied_at < (%s::date + INTERVAL '1 day')
                )
                ORDER BY known.developer_id
                """,
                (tenant_id, tenant_id, tenant_id, tenant_id, as_of, as_of),
            )
        return [str(row["developer_id"]) for row in rows]


class PostgresRollupRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def record_node_status(self, status: NodeStatus) -> None:
        with _tracer.start_as_current_span("postgres.rollup.record_node_status"):
            await self._executor.execute(
                """
                INSERT INTO node_statuses (
                    tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, entity_kind, entity_id, as_of)
                DO UPDATE SET
                    rag = EXCLUDED.rag,
                    source = EXCLUDED.source,
                    factors = EXCLUDED.factors,
                    updated_at = now()
                """,
                (
                    status.entity_ref.tenant_id,
                    status.entity_ref.kind.value,
                    status.entity_ref.id,
                    status.as_of,
                    status.rag.value,
                    status.source.value,
                    _factors_to_json(status.factors),
                ),
            )

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None:
        with _tracer.start_as_current_span("postgres.rollup.latest_node_status"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                FROM node_statuses
                WHERE tenant_id = %s
                  AND entity_kind = %s
                  AND entity_id = %s
                  AND as_of <= %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (tenant_id, entity_ref.kind.value, entity_ref.id, as_of),
            )
        return _node_status_from_row(rows[0]) if rows else None

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        with _tracer.start_as_current_span("postgres.rollup.list_node_statuses"):
            rows = await self._executor.fetch(
                """
                SELECT DISTINCT ON (entity_kind, entity_id)
                    tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                FROM node_statuses
                WHERE tenant_id = %s AND as_of <= %s
                ORDER BY entity_kind, entity_id, as_of DESC
                """,
                (tenant_id, as_of),
            )
        return [_node_status_from_row(row) for row in rows]


class PostgresSyncCursorRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        with _tracer.start_as_current_span("postgres.cursor.get_cursor"):
            rows = await self._executor.fetch(
                """
                SELECT cursor_value, cursor_updated_at, metadata
                FROM connector_sync_cursors
                WHERE tenant_id = %s AND connector = %s AND scope = %s
                """,
                (tenant_id, connector, scope),
            )
        return _sync_cursor_from_row(rows[0]) if rows else SyncCursor()

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        with _tracer.start_as_current_span("postgres.cursor.record_cursor"):
            await self._executor.execute(
                """
                INSERT INTO connector_sync_cursors (
                    tenant_id, connector, scope, cursor_value, cursor_updated_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, connector, scope)
                DO UPDATE SET
                    cursor_value = EXCLUDED.cursor_value,
                    cursor_updated_at = EXCLUDED.cursor_updated_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    tenant_id,
                    connector,
                    scope,
                    cursor.value,
                    cursor.updated_at,
                    _json_scalar_mapping(cursor.metadata),
                ),
            )


class PostgresConversationRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def append_turn(self, turn: ConversationTurn) -> None:
        with _tracer.start_as_current_span("postgres.conversation.append_turn"):
            await self._executor.execute(
                """
                INSERT INTO conversation_turns (
                    tenant_id, developer_id, conversation_id, conversation_date,
                    role, content, correlation_id, chat_message_id, observed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    turn.tenant_id,
                    turn.developer_id,
                    turn.conversation_id,
                    turn.conversation_date,
                    turn.role.value,
                    turn.content,
                    turn.correlation_id,
                    turn.chat_message_id,
                    turn.observed_at,
                ),
            )

    async def list_turns_for_day(
        self, tenant_id: str, developer_id: str, on: date
    ) -> list[ConversationTurn]:
        with _tracer.start_as_current_span("postgres.conversation.list_turns_for_day"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, conversation_id, conversation_date,
                       role, content, correlation_id, chat_message_id, observed_at
                FROM conversation_turns
                WHERE tenant_id = %s
                  AND developer_id = %s
                  AND conversation_date = %s
                ORDER BY observed_at ASC,
                         conversation_id ASC,
                         COALESCE(correlation_id, '') ASC,
                         COALESCE(chat_message_id, '') ASC,
                         role ASC,
                         content ASC
                """,
                (tenant_id, developer_id, on),
            )
        return [_conversation_turn_from_row(row) for row in rows]

    async def list_recent_turns(
        self,
        tenant_id: str,
        developer_id: str,
        limit: int,
        since: datetime | None = None,
    ) -> list[ConversationTurn]:
        if limit <= 0:
            return []
        with _tracer.start_as_current_span("postgres.conversation.list_recent_turns"):
            rows = await self._executor.fetch(
                """
                WITH latest AS (
                    SELECT tenant_id, developer_id, conversation_id, conversation_date,
                           role, content, correlation_id, chat_message_id, observed_at
                    FROM conversation_turns
                    WHERE tenant_id = %s
                      AND developer_id = %s
                      AND (%s::timestamptz IS NULL OR observed_at >= %s)
                    ORDER BY observed_at DESC,
                             conversation_id DESC,
                             COALESCE(correlation_id, '') DESC,
                             COALESCE(chat_message_id, '') DESC,
                             role DESC,
                             content DESC
                    LIMIT %s
                )
                SELECT tenant_id, developer_id, conversation_id, conversation_date,
                       role, content, correlation_id, chat_message_id, observed_at
                FROM latest
                ORDER BY observed_at ASC,
                         conversation_id ASC,
                         COALESCE(correlation_id, '') ASC,
                         COALESCE(chat_message_id, '') ASC,
                         role ASC,
                         content ASC
                """,
                (tenant_id, developer_id, since, since, limit),
            )
        return [_conversation_turn_from_row(row) for row in rows]

    async def user_turn_exists(
        self, tenant_id: str, developer_id: str, chat_message_id: str
    ) -> bool:
        with _tracer.start_as_current_span("postgres.conversation.user_turn_exists"):
            rows = await self._executor.fetch(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM conversation_turns
                    WHERE tenant_id = %s
                      AND developer_id = %s
                      AND role = 'user'
                      AND chat_message_id = %s
                    LIMIT 1
                ) AS user_turn_exists
                """,
                (tenant_id, developer_id, chat_message_id),
            )
        return bool(rows and rows[0]["user_turn_exists"])

    async def purge_turns_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        with _tracer.start_as_current_span("postgres.conversation.purge_turns_older_than"):
            rows = await self._executor.fetch(
                """
                WITH deleted AS (
                    DELETE FROM conversation_turns
                    WHERE tenant_id = %s AND observed_at < %s
                    RETURNING 1
                )
                SELECT count(*) AS deleted_count
                FROM deleted
                """,
                (tenant_id, cutoff),
            )
        return _int_field(rows[0]["deleted_count"], "deleted_count") if rows else 0


def _checkin_from_row(row: Mapping[str, object]) -> CheckIn:
    raw_reply = row.get("raw_reply")
    return CheckIn(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        correlation_id=str(row["correlation_id"]),
        asked_at=_datetime_field(row["asked_at"], "asked_at"),
        replied_at=_optional_datetime_field(row.get("replied_at"), "replied_at"),
        raw_reply=raw_reply if isinstance(raw_reply, str) else None,
        signals=_signals_from_json(row.get("signals")),
    )


def _checkin_correlation_from_row(row: Mapping[str, object]) -> CheckInCorrelation:
    return CheckInCorrelation(
        tenant_id=str(row["tenant_id"]),
        correlation_id=str(row["correlation_id"]),
        developer_id=str(row["developer_id"]),
        chat_user_ref=str(row["chat_user_ref"]),
        chat_thread_ref=str(row["chat_thread_ref"]),
        outbound_message_id=str(row["outbound_message_id"]),
        asked_at=_datetime_field(row["asked_at"], "asked_at"),
        consumed_at=_optional_datetime_field(row.get("consumed_at"), "consumed_at"),
    )


def _checkin_preference_from_row(row: Mapping[str, object]) -> CheckInPreference:
    timezone = row.get("timezone")
    return CheckInPreference(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        local_time=_time_field(row["local_time"], "local_time"),
        timezone=timezone if isinstance(timezone, str) and timezone else None,
        weekdays=_int_tuple_from_json(row.get("weekdays")),
        reply_wait_seconds=_int_field(row["reply_wait_seconds"], "reply_wait_seconds"),
        final_reply_wait_seconds=_int_field(
            row["final_reply_wait_seconds"],
            "final_reply_wait_seconds",
        ),
    )


def _checkin_schedule_run_from_row(row: Mapping[str, object]) -> CheckInScheduleRun:
    reason = row.get("reason")
    return CheckInScheduleRun(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        checkin_date=_date_field(row["checkin_date"], "checkin_date"),
        correlation_id=str(row["correlation_id"]),
        status=str(row["status"]),
        scheduled_at=_datetime_field(row["scheduled_at"], "scheduled_at"),
        reason=reason if isinstance(reason, str) else None,
    )


def _checkin_nudge_from_row(row: Mapping[str, object]) -> CheckInNudge:
    outbound_message_id = row.get("outbound_message_id")
    return CheckInNudge(
        tenant_id=str(row["tenant_id"]),
        correlation_id=str(row["correlation_id"]),
        nudge_number=_int_field(row["nudge_number"], "nudge_number"),
        sent_at=_optional_datetime_field(row.get("sent_at"), "sent_at"),
        outbound_message_id=outbound_message_id if isinstance(outbound_message_id, str) else None,
    )


def _checkin_clarification_from_row(row: Mapping[str, object]) -> CheckInClarification:
    outbound_message_id = row.get("outbound_message_id")
    return CheckInClarification(
        tenant_id=str(row["tenant_id"]),
        correlation_id=str(row["correlation_id"]),
        clarification_number=_int_field(
            row["clarification_number"],
            "clarification_number",
        ),
        question=str(row["question"]),
        sent_at=_optional_datetime_field(row.get("sent_at"), "sent_at"),
        outbound_message_id=outbound_message_id if isinstance(outbound_message_id, str) else None,
    )


def _developer_status_from_row(row: Mapping[str, object]) -> DeveloperStatus:
    eta_change_days = row.get("eta_change_days")
    return DeveloperStatus(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        as_of=_date_field(row["as_of"], "as_of"),
        source=StatusSource(str(row["source"])),
        blockers=_string_tuple_from_json(row.get("blockers")),
        summary=str(row["summary"]),
        eta_change_days=eta_change_days
        if isinstance(eta_change_days, int) and not isinstance(eta_change_days, bool)
        else None,
        mood=_mood_from_json(row.get("mood")),
    )


def _node_status_from_row(row: Mapping[str, object]) -> NodeStatus:
    return NodeStatus(
        entity_ref=EntityRef(
            tenant_id=str(row["tenant_id"]),
            kind=NodeKind(str(row["entity_kind"])),
            id=str(row["entity_id"]),
        ),
        rag=Rag(str(row["rag"])),
        source=StatusSource(str(row["source"])),
        factors=_factors_from_json(row.get("factors")),
        as_of=_date_field(row["as_of"], "as_of"),
    )


def _sync_cursor_from_row(row: Mapping[str, object]) -> SyncCursor:
    cursor_value = row.get("cursor_value")
    return SyncCursor(
        value=cursor_value if isinstance(cursor_value, str) else None,
        updated_at=_optional_datetime_field(row.get("cursor_updated_at"), "cursor_updated_at"),
        metadata=_json_scalar_mapping(row.get("metadata")),
    )


def _conversation_turn_from_row(row: Mapping[str, object]) -> ConversationTurn:
    correlation_id = row.get("correlation_id")
    chat_message_id = row.get("chat_message_id")
    return ConversationTurn(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        conversation_id=str(row["conversation_id"]),
        conversation_date=_date_field(row["conversation_date"], "conversation_date"),
        role=ConversationRole(str(row["role"])),
        content=str(row["content"]),
        correlation_id=correlation_id if isinstance(correlation_id, str) else None,
        chat_message_id=chat_message_id if isinstance(chat_message_id, str) else None,
        observed_at=_datetime_field(row["observed_at"], "observed_at"),
    )


def _signals_to_json(signals: CheckInSignals | None) -> dict[str, object] | None:
    if signals is None:
        return None
    return {
        "progress_note": signals.progress_note,
        "blockers": list(signals.blockers),
        "eta_change_days": signals.eta_change_days,
        "mood": signals.mood.value if signals.mood else None,
    }


def _signals_from_json(value: object) -> CheckInSignals | None:
    if not isinstance(value, Mapping):
        return None
    progress_note = value.get("progress_note")
    if not isinstance(progress_note, str):
        return None
    eta_change_days = value.get("eta_change_days")
    return CheckInSignals(
        progress_note=progress_note,
        blockers=_string_tuple_from_json(value.get("blockers")),
        eta_change_days=eta_change_days
        if isinstance(eta_change_days, int) and not isinstance(eta_change_days, bool)
        else None,
        mood=_mood_from_json(value.get("mood")),
    )


def _string_tuple_to_json(values: tuple[str, ...]) -> dict[str, object]:
    return {"items": list(values)}


def _int_tuple_to_json(values: tuple[int, ...]) -> dict[str, object]:
    return {"items": list(values)}


def _string_tuple_from_json(value: object) -> tuple[str, ...]:
    items = _items_from_json(value)
    return tuple(item for item in items if isinstance(item, str))


def _int_tuple_from_json(value: object) -> tuple[int, ...]:
    items = _items_from_json(value)
    return tuple(item for item in items if isinstance(item, int) and not isinstance(item, bool))


def _factors_to_json(factors: tuple[RollupFactor, ...]) -> dict[str, object]:
    return {"items": [_factor_to_json(factor) for factor in factors]}


def _factor_to_json(factor: RollupFactor) -> dict[str, object]:
    return {
        "description": factor.description,
        "contributes": factor.contributes.value,
        "source_ref": {
            "tenant_id": factor.source_ref.tenant_id,
            "kind": factor.source_ref.kind.value,
            "id": factor.source_ref.id,
        },
    }


def _factors_from_json(value: object) -> tuple[RollupFactor, ...]:
    factors: list[RollupFactor] = []
    for item in _items_from_json(value):
        factor = _factor_from_json(item)
        if factor is not None:
            factors.append(factor)
    return tuple(factors)


def _factor_from_json(value: object) -> RollupFactor | None:
    if not isinstance(value, Mapping):
        return None
    description = value.get("description")
    contributes = _rag_from_json(value.get("contributes"))
    source_ref = _entity_ref_from_json(value.get("source_ref"))
    if not isinstance(description, str) or contributes is None or source_ref is None:
        return None
    return RollupFactor(
        description=description,
        contributes=contributes,
        source_ref=source_ref,
    )


def _entity_ref_from_json(value: object) -> EntityRef | None:
    if not isinstance(value, Mapping):
        return None
    tenant_id = value.get("tenant_id")
    kind = value.get("kind")
    entity_id = value.get("id")
    if (
        not isinstance(tenant_id, str)
        or not isinstance(kind, str)
        or not isinstance(entity_id, str)
    ):
        return None
    try:
        node_kind = NodeKind(kind)
    except ValueError:
        return None
    return EntityRef(tenant_id=tenant_id, kind=node_kind, id=entity_id)


def _items_from_json(value: object) -> tuple[object, ...]:
    raw_items = value.get("items") if isinstance(value, Mapping) else value
    if isinstance(raw_items, list | tuple):
        return tuple(raw_items)
    return ()


def _mood_from_json(value: object) -> Mood | None:
    if not isinstance(value, str):
        return None
    try:
        return Mood(value)
    except ValueError:
        return None


def _rag_from_json(value: object) -> Rag | None:
    if not isinstance(value, str):
        return None
    try:
        return Rag(value)
    except ValueError:
        return None


def _json_scalar_mapping(value: object) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if isinstance(key, str) and (item is None or isinstance(item, str | int | float | bool)):
            result[key] = item
    return result


def _datetime_field(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime instance")


def _optional_datetime_field(value: object, field_name: str) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime instance or None")


def _date_field(value: object, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a date instance")


def _time_field(value: object, field_name: str) -> time:
    if isinstance(value, time):
        return value
    raise TypeError(f"{field_name} must be a time instance")


def _int_field(value: object, field_name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"{field_name} must be an int instance")
