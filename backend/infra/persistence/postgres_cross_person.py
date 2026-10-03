from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import EntityRef, NodeKind

_tracer = trace.get_tracer("openprogram.persistence.cross_person")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresCrossPersonRequestRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def create(self, request: CrossPersonRequest) -> CrossPersonRequest:
        with _tracer.start_as_current_span("postgres.cross_person.create"):
            rows = await self._executor.fetch(
                """
                INSERT INTO cross_person_requests (
                    tenant_id, id, requester_id, requester_chat_ref, counterpart_id,
                    kind, note, task_kind, task_id, source_correlation_id, status,
                    created_at, updated_at, raw_name, email, counterpart_display_name,
                    counterpart_email, notify_message_id, notify_correlation_id,
                    notify_attempts, notify_last_attempt_at, notify_next_attempt_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (tenant_id, id)
                DO UPDATE SET updated_at = cross_person_requests.updated_at
                RETURNING *
                """,
                _request_params(request),
            )
        return _request_from_row(rows[0])

    async def get(self, tenant_id: str, request_id: str) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.get"):
            rows = await self._executor.fetch(
                """
                SELECT *
                FROM cross_person_requests
                WHERE tenant_id = %s AND id = %s
                LIMIT 1
                """,
                (tenant_id, request_id),
            )
        return _request_from_row(rows[0]) if rows else None

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.update_status"):
            rows = await self._executor.fetch(
                """
                UPDATE cross_person_requests
                SET status = %s, updated_at = %s
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (status.value, updated_at, tenant_id, request_id),
            )
        return _request_from_row(rows[0]) if rows else None

    async def record_notification(
        self,
        tenant_id: str,
        request_id: str,
        *,
        notify_message_id: str,
        notify_correlation_id: str,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.record_notification"):
            rows = await self._executor.fetch(
                """
                UPDATE cross_person_requests
                SET notify_message_id = %s,
                    notify_correlation_id = %s,
                    notify_next_attempt_at = NULL,
                    updated_at = %s
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (notify_message_id, notify_correlation_id, updated_at, tenant_id, request_id),
            )
        return _request_from_row(rows[0]) if rows else None

    async def assign_counterpart(
        self,
        tenant_id: str,
        request_id: str,
        *,
        counterpart_id: str,
        counterpart_display_name: str | None,
        counterpart_email: str | None,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.assign_counterpart"):
            rows = await self._executor.fetch(
                """
                UPDATE cross_person_requests
                SET counterpart_id = %s,
                    counterpart_display_name = %s,
                    counterpart_email = %s,
                    status = %s,
                    updated_at = %s
                WHERE tenant_id = %s AND id = %s
                  AND status = %s
                RETURNING *
                """,
                (
                    counterpart_id,
                    counterpart_display_name,
                    counterpart_email,
                    CrossPersonRequestStatus.OPEN.value,
                    updated_at,
                    tenant_id,
                    request_id,
                    CrossPersonRequestStatus.NEEDS_RESOLUTION.value,
                ),
            )
        return _request_from_row(rows[0]) if rows else None

    async def claim_notification_attempt(
        self,
        tenant_id: str,
        request_id: str,
        *,
        expected_attempts: int,
        attempted_at: datetime,
        next_attempt_at: datetime | None,
    ) -> CrossPersonRequest | None:
        # One conditional UPDATE is the claim. A second sender that read the
        # same row blocks on the row lock, then re-checks the WHERE against the
        # committed row: the attempt count has moved on (or a notification has
        # been recorded), so it updates nothing and sends nothing.
        with _tracer.start_as_current_span("postgres.cross_person.claim_notification_attempt"):
            rows = await self._executor.fetch(
                """
                UPDATE cross_person_requests
                SET notify_attempts = notify_attempts + 1,
                    notify_last_attempt_at = %s,
                    notify_next_attempt_at = %s
                WHERE tenant_id = %s AND id = %s
                  AND status = %s
                  AND counterpart_id IS NOT NULL
                  AND notify_message_id IS NULL
                  AND notify_correlation_id IS NULL
                  AND notify_attempts = %s
                RETURNING *
                """,
                (
                    attempted_at,
                    next_attempt_at,
                    tenant_id,
                    request_id,
                    CrossPersonRequestStatus.OPEN.value,
                    expected_attempts,
                ),
            )
        return _request_from_row(rows[0]) if rows else None

    async def list_notification_retries_due(
        self,
        tenant_id: str,
        *,
        due_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> list[CrossPersonRequest]:
        with _tracer.start_as_current_span("postgres.cross_person.list_notification_retries_due"):
            rows = await self._executor.fetch(
                """
                SELECT *
                FROM cross_person_requests
                WHERE tenant_id = %s
                  AND status = %s
                  AND counterpart_id IS NOT NULL
                  AND notify_message_id IS NULL
                  AND notify_correlation_id IS NULL
                  AND notify_attempts > 0
                  AND notify_attempts < %s
                  AND notify_next_attempt_at IS NOT NULL
                  AND notify_next_attempt_at <= %s
                ORDER BY notify_next_attempt_at ASC, id ASC
                LIMIT %s
                """,
                (
                    tenant_id,
                    CrossPersonRequestStatus.OPEN.value,
                    max_attempts,
                    due_at,
                    max(0, limit),
                ),
            )
        return [_request_from_row(row) for row in rows]

    async def list_for_counterpart(
        self,
        tenant_id: str,
        counterpart_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        clauses = ["tenant_id = %s", "counterpart_id = %s"]
        params: list[object] = [tenant_id, counterpart_id]
        _append_status_filter(clauses, params, statuses)
        return await self._list(clauses, params)

    async def list_for_requester(
        self,
        tenant_id: str,
        requester_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        clauses = ["tenant_id = %s", "requester_id = %s"]
        params: list[object] = [tenant_id, requester_id]
        _append_status_filter(clauses, params, statuses)
        return await self._list(clauses, params)

    async def list_open(self, tenant_id: str) -> list[CrossPersonRequest]:
        return await self._list(
            ["tenant_id = %s"],
            [tenant_id],
            statuses=(
                CrossPersonRequestStatus.OPEN,
                CrossPersonRequestStatus.ACKNOWLEDGED,
                CrossPersonRequestStatus.NEEDS_RESOLUTION,
            ),
        )

    async def get_by_notify_correlation(
        self,
        tenant_id: str,
        notify_correlation_id: str,
    ) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.get_by_notify_correlation"):
            rows = await self._executor.fetch(
                """
                SELECT *
                FROM cross_person_requests
                WHERE tenant_id = %s AND notify_correlation_id = %s
                LIMIT 1
                """,
                (tenant_id, notify_correlation_id),
            )
        return _request_from_row(rows[0]) if rows else None

    async def get_by_notify_message_id(
        self,
        tenant_id: str,
        message_id: str,
    ) -> CrossPersonRequest | None:
        with _tracer.start_as_current_span("postgres.cross_person.get_by_notify_message_id"):
            rows = await self._executor.fetch(
                """
                SELECT *
                FROM cross_person_requests
                WHERE tenant_id = %s AND notify_message_id = %s
                LIMIT 1
                """,
                (tenant_id, message_id),
            )
        return _request_from_row(rows[0]) if rows else None

    async def _list(
        self,
        clauses: list[str],
        params: list[object],
        *,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        _append_status_filter(clauses, params, statuses)
        with _tracer.start_as_current_span("postgres.cross_person.list"):
            rows = await self._executor.fetch(
                f"""
                SELECT *
                FROM cross_person_requests
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, updated_at DESC, id DESC
                """,
                tuple(params),
            )
        return [_request_from_row(row) for row in rows]


def _append_status_filter(
    clauses: list[str],
    params: list[object],
    statuses: Sequence[CrossPersonRequestStatus] | None,
) -> None:
    if statuses is None:
        return
    if not statuses:
        clauses.append("FALSE")
        return
    placeholders = ", ".join(["%s"] * len(statuses))
    clauses.append(f"status IN ({placeholders})")
    params.extend(status.value for status in statuses)


def _request_params(request: CrossPersonRequest) -> tuple[object, ...]:
    return (
        request.tenant_id,
        request.id,
        request.requester_id,
        request.requester_chat_ref,
        request.counterpart_id,
        request.kind.value,
        request.note,
        request.task_ref.kind.value if request.task_ref is not None else None,
        request.task_ref.id if request.task_ref is not None else None,
        request.source_correlation_id,
        request.status.value,
        request.created_at,
        request.updated_at,
        request.raw_name,
        request.email,
        request.counterpart_display_name,
        request.counterpart_email,
        request.notify_message_id,
        request.notify_correlation_id,
        request.notify_attempts,
        request.notify_last_attempt_at,
        request.notify_next_attempt_at,
    )


def _request_from_row(row: Mapping[str, object]) -> CrossPersonRequest:
    task_ref = _task_ref_from_row(row)
    return CrossPersonRequest(
        tenant_id=str(row["tenant_id"]),
        id=str(row["id"]),
        requester_id=str(row["requester_id"]),
        requester_chat_ref=_optional_string(row.get("requester_chat_ref")),
        counterpart_id=_optional_string(row.get("counterpart_id")),
        kind=CrossPersonRequestKind(str(row["kind"])),
        note=str(row["note"]),
        task_ref=task_ref,
        source_correlation_id=str(row["source_correlation_id"]),
        status=CrossPersonRequestStatus(str(row["status"])),
        created_at=_datetime_field(row["created_at"], "created_at"),
        updated_at=_datetime_field(row["updated_at"], "updated_at"),
        raw_name=_optional_string(row.get("raw_name")),
        email=_optional_string(row.get("email")),
        counterpart_display_name=_optional_string(row.get("counterpart_display_name")),
        counterpart_email=_optional_string(row.get("counterpart_email")),
        notify_message_id=_optional_string(row.get("notify_message_id")),
        notify_correlation_id=_optional_string(row.get("notify_correlation_id")),
        notify_attempts=_int_field(row.get("notify_attempts")),
        notify_last_attempt_at=_optional_datetime(row.get("notify_last_attempt_at")),
        notify_next_attempt_at=_optional_datetime(row.get("notify_next_attempt_at")),
    )


def _task_ref_from_row(row: Mapping[str, object]) -> EntityRef | None:
    task_kind = _optional_string(row.get("task_kind"))
    task_id = _optional_string(row.get("task_id"))
    if task_kind is None or task_id is None:
        return None
    return EntityRef(tenant_id=str(row["tenant_id"]), kind=NodeKind(task_kind), id=task_id)


def _datetime_field(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _int_field(value: object) -> int:
    # A row read before the attempts column existed has no value: no attempts.
    return value if isinstance(value, int) else 0
