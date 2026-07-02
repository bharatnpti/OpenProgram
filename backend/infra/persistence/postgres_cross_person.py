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

_tracer = trace.get_tracer("pulseops.persistence.cross_person")


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
                    counterpart_email, notify_message_id, notify_correlation_id
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
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
                    updated_at = %s
                WHERE tenant_id = %s AND id = %s
                RETURNING *
                """,
                (notify_message_id, notify_correlation_id, updated_at, tenant_id, request_id),
            )
        return _request_from_row(rows[0]) if rows else None

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
