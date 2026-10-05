from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.auth import Role
from core.domain.delivery import DeliveryStage
from core.domain.gates import (
    GateItem,
    GateTemplate,
    ItemKind,
    ItemSource,
    ItemStatus,
    QuestionStatus,
    TrackedQuestion,
)

_tracer = trace.get_tracer("openprogram.persistence.gates")

_TEMPLATE_COLUMNS = (
    "tenant_id, template_id, name, guards_stage, kinds, issue_types, enabled, updated_at, "
    "updated_by"
)
_ITEM_COLUMNS = (
    "tenant_id, item_id, issue_key, template_id, kind, text, status, source, source_ref, "
    "fingerprint, created_at, updated_at, created_by, signed_by, signed_at, evidence_url, note"
)
_QUESTION_COLUMNS = (
    "tenant_id, question_id, issue_key, comment_ref, asked_by, asked_by_name, asked_to, "
    "asked_to_name, asked_at, summary, status, confirmed, status_set_by_person, answered_ref, "
    "dismissed, updated_at, updated_by"
)


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresGateTemplateRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list(self, tenant_id: str) -> list[GateTemplate]:
        with _tracer.start_as_current_span("postgres.gates.list_templates"):
            rows = await self._executor.fetch(
                f"SELECT {_TEMPLATE_COLUMNS} FROM gate_templates WHERE tenant_id = %s "
                "ORDER BY name",
                (tenant_id,),
            )
        return [_template(row) for row in rows]

    async def save(self, template: GateTemplate) -> None:
        with _tracer.start_as_current_span("postgres.gates.save_template"):
            await self._executor.execute(
                f"""
                INSERT INTO gate_templates ({_TEMPLATE_COLUMNS})
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, template_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    guards_stage = EXCLUDED.guards_stage,
                    kinds = EXCLUDED.kinds,
                    issue_types = EXCLUDED.issue_types,
                    enabled = EXCLUDED.enabled,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    template.tenant_id,
                    template.template_id,
                    template.name,
                    template.guards_stage.value,
                    json.dumps([kind_to_json(kind) for kind in template.kinds]),
                    list(template.issue_types),
                    template.enabled,
                    template.updated_at,
                    template.updated_by or "",
                ),
            )

    async def delete(self, tenant_id: str, template_id: str) -> bool:
        with _tracer.start_as_current_span("postgres.gates.delete_template"):
            rows = await self._executor.fetch(
                "DELETE FROM gate_templates WHERE tenant_id = %s AND template_id = %s "
                "RETURNING template_id",
                (tenant_id, template_id),
            )
        return bool(rows)


class PostgresGateItemRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_for_issues(self, tenant_id: str, issue_keys: Sequence[str]) -> list[GateItem]:
        if not issue_keys:
            return []
        with _tracer.start_as_current_span("postgres.gates.list_items"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_ITEM_COLUMNS} FROM gate_items
                WHERE tenant_id = %s AND issue_key = ANY(%s)
                ORDER BY issue_key, created_at
                """,
                (tenant_id, list(issue_keys)),
            )
        return [_item(row) for row in rows]

    async def get(self, tenant_id: str, item_id: str) -> GateItem | None:
        with _tracer.start_as_current_span("postgres.gates.get_item"):
            rows = await self._executor.fetch(
                f"SELECT {_ITEM_COLUMNS} FROM gate_items WHERE tenant_id = %s AND item_id = %s",
                (tenant_id, item_id),
            )
        return _item(rows[0]) if rows else None

    async def save(self, item: GateItem) -> None:
        with _tracer.start_as_current_span("postgres.gates.save_item"):
            await self._executor.execute(
                f"""
                INSERT INTO gate_items ({_ITEM_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, item_id) DO UPDATE SET
                    text = EXCLUDED.text,
                    status = EXCLUDED.status,
                    fingerprint = EXCLUDED.fingerprint,
                    updated_at = EXCLUDED.updated_at,
                    signed_by = EXCLUDED.signed_by,
                    signed_at = EXCLUDED.signed_at,
                    evidence_url = EXCLUDED.evidence_url,
                    note = EXCLUDED.note
                """,
                (
                    item.tenant_id,
                    item.item_id,
                    item.issue_key,
                    item.template_id,
                    item.kind,
                    item.text,
                    item.status.value,
                    item.source.value,
                    item.source_ref,
                    item.fingerprint,
                    item.created_at,
                    item.updated_at,
                    item.created_by,
                    item.signed_by,
                    item.signed_at,
                    item.evidence_url,
                    item.note,
                ),
            )


class PostgresQuestionRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_for_issues(
        self, tenant_id: str, issue_keys: Sequence[str]
    ) -> list[TrackedQuestion]:
        if not issue_keys:
            return []
        with _tracer.start_as_current_span("postgres.gates.list_questions"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_QUESTION_COLUMNS} FROM tracked_questions
                WHERE tenant_id = %s AND issue_key = ANY(%s)
                ORDER BY asked_at
                """,
                (tenant_id, list(issue_keys)),
            )
        return [_question(row) for row in rows]

    async def get(self, tenant_id: str, question_id: str) -> TrackedQuestion | None:
        with _tracer.start_as_current_span("postgres.gates.get_question"):
            rows = await self._executor.fetch(
                f"SELECT {_QUESTION_COLUMNS} FROM tracked_questions "
                "WHERE tenant_id = %s AND question_id = %s",
                (tenant_id, question_id),
            )
        return _question(rows[0]) if rows else None

    async def save(self, question: TrackedQuestion) -> None:
        with _tracer.start_as_current_span("postgres.gates.save_question"):
            await self._executor.execute(
                f"""
                INSERT INTO tracked_questions ({_QUESTION_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, question_id) DO UPDATE SET
                    asked_to = EXCLUDED.asked_to,
                    asked_to_name = EXCLUDED.asked_to_name,
                    summary = EXCLUDED.summary,
                    status = EXCLUDED.status,
                    confirmed = EXCLUDED.confirmed,
                    status_set_by_person = EXCLUDED.status_set_by_person,
                    answered_ref = EXCLUDED.answered_ref,
                    dismissed = EXCLUDED.dismissed,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    question.tenant_id,
                    question.question_id,
                    question.issue_key,
                    question.comment_ref,
                    question.asked_by,
                    question.asked_by_name,
                    question.asked_to,
                    question.asked_to_name,
                    question.asked_at,
                    question.summary,
                    question.status.value,
                    question.confirmed,
                    question.status_set_by_person,
                    question.answered_ref,
                    question.dismissed,
                    question.updated_at,
                    question.updated_by,
                ),
            )


class PostgresIssueScanRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def last_scanned(self, tenant_id: str, issue_key: str) -> datetime | None:
        with _tracer.start_as_current_span("postgres.gates.last_scanned"):
            rows = await self._executor.fetch(
                "SELECT issue_updated_at, scanned_at FROM issue_scans "
                "WHERE tenant_id = %s AND issue_key = %s",
                (tenant_id, issue_key),
            )
        if not rows:
            return None
        value = rows[0].get("issue_updated_at") or rows[0].get("scanned_at")
        return value if isinstance(value, datetime) else None

    async def record(
        self,
        tenant_id: str,
        issue_key: str,
        issue_updated_at: datetime | None,
        scanned_at: datetime,
    ) -> None:
        with _tracer.start_as_current_span("postgres.gates.record_scan"):
            await self._executor.execute(
                """
                INSERT INTO issue_scans (tenant_id, issue_key, issue_updated_at, scanned_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, issue_key) DO UPDATE SET
                    issue_updated_at = EXCLUDED.issue_updated_at,
                    scanned_at = EXCLUDED.scanned_at
                """,
                (tenant_id, issue_key, issue_updated_at, scanned_at),
            )


def kind_to_json(kind: ItemKind) -> dict[str, object]:
    return {
        "key": kind.key,
        "label": kind.label,
        "sign_off_roles": [role.value for role in kind.sign_off_roles],
        "evidence_required": kind.evidence_required,
        "headings": list(kind.headings),
        "gherkin": kind.gherkin,
    }


def kind_from_json(value: Mapping[str, object]) -> ItemKind:
    roles = []
    for role in value.get("sign_off_roles") or []:  # type: ignore[attr-defined]
        try:
            roles.append(Role(str(role)))
        except ValueError:
            continue
    headings = value.get("headings")
    return ItemKind(
        key=str(value.get("key") or ""),
        label=str(value.get("label") or ""),
        sign_off_roles=tuple(roles),
        evidence_required=bool(value.get("evidence_required")),
        headings=tuple(str(item) for item in headings) if isinstance(headings, list) else (),
        gherkin=bool(value.get("gherkin")),
    )


def _template(row: Mapping[str, object]) -> GateTemplate:
    kinds = row.get("kinds")
    if isinstance(kinds, str):
        kinds = json.loads(kinds)
    issue_types = row.get("issue_types")
    updated_at = row.get("updated_at")
    return GateTemplate(
        tenant_id=str(row["tenant_id"]),
        template_id=str(row["template_id"]),
        name=str(row["name"]),
        guards_stage=DeliveryStage(str(row["guards_stage"])),
        kinds=tuple(
            kind_from_json(item)
            for item in (kinds if isinstance(kinds, list) else [])
            if isinstance(item, Mapping)
        ),
        issue_types=tuple(str(item) for item in issue_types)
        if isinstance(issue_types, list | tuple)
        else (),
        enabled=bool(row.get("enabled", True)),
        updated_at=updated_at if isinstance(updated_at, datetime) else None,
        updated_by=str(row.get("updated_by") or "") or None,
    )


def _item(row: Mapping[str, object]) -> GateItem:
    signed_at = row.get("signed_at")
    created_at = row["created_at"]
    updated_at = row["updated_at"]
    if not isinstance(created_at, datetime) or not isinstance(updated_at, datetime):
        raise TypeError("gate_items row has no timestamps")
    evidence = row.get("evidence_url")
    signed_by = row.get("signed_by")
    return GateItem(
        tenant_id=str(row["tenant_id"]),
        item_id=str(row["item_id"]),
        issue_key=str(row["issue_key"]),
        template_id=str(row["template_id"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        status=ItemStatus(str(row["status"])),
        source=ItemSource(str(row["source"])),
        source_ref=str(row.get("source_ref") or ""),
        created_at=created_at,
        updated_at=updated_at,
        created_by=str(row["created_by"]),
        signed_by=signed_by if isinstance(signed_by, str) else None,
        signed_at=signed_at if isinstance(signed_at, datetime) else None,
        evidence_url=evidence if isinstance(evidence, str) else None,
        note=str(row.get("note") or ""),
    )


def _question(row: Mapping[str, object]) -> TrackedQuestion:
    asked_at = row["asked_at"]
    updated_at = row.get("updated_at")
    if not isinstance(asked_at, datetime):
        raise TypeError("tracked_questions row has no asked_at")
    answered = row.get("answered_ref")
    updated_by = row.get("updated_by")
    return TrackedQuestion(
        tenant_id=str(row["tenant_id"]),
        question_id=str(row["question_id"]),
        issue_key=str(row["issue_key"]),
        comment_ref=str(row.get("comment_ref") or ""),
        asked_by=str(row["asked_by"]),
        asked_by_name=str(row.get("asked_by_name") or ""),
        asked_to=str(row.get("asked_to") or ""),
        asked_to_name=str(row.get("asked_to_name") or ""),
        asked_at=asked_at,
        summary=str(row["summary"]),
        status=QuestionStatus(str(row["status"])),
        confirmed=bool(row.get("confirmed")),
        status_set_by_person=bool(row.get("status_set_by_person")),
        answered_ref=answered if isinstance(answered, str) else None,
        dismissed=bool(row.get("dismissed")),
        updated_at=updated_at if isinstance(updated_at, datetime) else None,
        updated_by=updated_by if isinstance(updated_by, str) else None,
    )
