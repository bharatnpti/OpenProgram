from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.delivery import DeliveryStage
from core.domain.hygiene import (
    Condition,
    ConditionKind,
    HygieneAction,
    HygieneFinding,
    HygieneRule,
    HygieneRun,
    HygieneSettings,
    HygieneState,
    IssueField,
    Owner,
    OwnerKind,
    PersonDecision,
    Resolution,
    RuleNeed,
    RuleScope,
    RuleSeverity,
    Select,
    StageFilter,
)
from core.domain.release_readiness import (
    Matcher,
    MatcherKind,
    RunStatus,
    RunTrigger,
    ScopeKind,
    ScopeRef,
    Strength,
)

_tracer = trace.get_tracer("openprogram.persistence.hygiene")

_RULE_COLUMNS = (
    "tenant_id, rule_id, version, name, spec, enabled, deleted_at, updated_by, updated_at"
)
_FINDING_COLUMNS = (
    "tenant_id, id, rule_id, rule_version, issue_key, scope_kind, scope_id, project_id, state, "
    "owner_member_id, detail, fingerprint, held, first_seen_at, last_seen_at, resolved_at, "
    "resolution, decided_by, decided_at, decision_reason"
)
_ACTION_COLUMNS = "tenant_id, id, finding_id, rule_id, action, actor, at, note, detail"
_RUN_COLUMNS = (
    "tenant_id, id, slot, trigger, status, started_at, finished_at, counts, data_as_of, "
    "error_category, actor"
)


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresHygieneRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    # ---- settings and rules -----------------------------------------------------------

    async def get_settings(self, tenant_id: str) -> HygieneSettings | None:
        with _tracer.start_as_current_span("postgres.hygiene.get_settings"):
            rows = await self._executor.fetch(
                "SELECT tenant_id, enabled, change_labels, stale_working_days, updated_by, "
                "updated_at FROM hygiene_settings WHERE tenant_id = %s",
                (tenant_id,),
            )
        if not rows:
            return None
        row = rows[0]
        return HygieneSettings(
            tenant_id=str(row["tenant_id"]),
            enabled=bool(row.get("enabled")),
            change_labels=_strings(_json(row.get("change_labels"))),
            stale_working_days=int(str(row.get("stale_working_days") or 5)),
            updated_at=_moment(row.get("updated_at")),
            updated_by=_optional_text(row.get("updated_by")),
        )

    async def save_settings(self, settings: HygieneSettings) -> None:
        with _tracer.start_as_current_span("postgres.hygiene.save_settings"):
            await self._executor.execute(
                """
                INSERT INTO hygiene_settings
                    (tenant_id, enabled, change_labels, stale_working_days, updated_by, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    change_labels = EXCLUDED.change_labels,
                    stale_working_days = EXCLUDED.stale_working_days,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    settings.tenant_id,
                    settings.enabled,
                    json.dumps(list(settings.change_labels)),
                    settings.stale_working_days,
                    settings.updated_by or "",
                    settings.updated_at,
                ),
            )

    async def list_rules(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[HygieneRule]:
        deleted = "" if include_deleted else " AND deleted_at IS NULL"
        with _tracer.start_as_current_span("postgres.hygiene.list_rules"):
            rows = await self._executor.fetch(
                f"SELECT {_RULE_COLUMNS} FROM hygiene_rules "
                f"WHERE tenant_id = %s{deleted} ORDER BY lower(name)",
                (tenant_id,),
            )
        return [_rule(row) for row in rows]

    async def save_rule(self, rule: HygieneRule) -> None:
        with _tracer.start_as_current_span("postgres.hygiene.save_rule"):
            await self._executor.execute(
                f"""
                INSERT INTO hygiene_rules ({_RULE_COLUMNS})
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, rule_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    name = EXCLUDED.name,
                    spec = EXCLUDED.spec,
                    enabled = EXCLUDED.enabled,
                    deleted_at = EXCLUDED.deleted_at,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    rule.tenant_id,
                    rule.rule_id,
                    rule.version,
                    rule.name,
                    json.dumps(rule_spec(rule)),
                    rule.enabled,
                    rule.deleted_at,
                    rule.updated_by or "",
                    rule.updated_at,
                ),
            )

    # ---- findings ---------------------------------------------------------------------

    async def list_findings(
        self,
        tenant_id: str,
        scopes: Sequence[ScopeRef] | None = None,
        *,
        states: Collection[HygieneState] | None = None,
    ) -> list[HygieneFinding]:
        if scopes is not None and not scopes:
            return []
        clauses = ["tenant_id = %s"]
        params: list[object] = [tenant_id]
        if scopes is not None:
            clauses.append("(scope_kind || ':' || scope_id) = ANY(%s)")
            params.append([scope.key for scope in scopes])
        if states is not None:
            clauses.append("state = ANY(%s)")
            params.append([state.value for state in states])
        with _tracer.start_as_current_span("postgres.hygiene.list_findings"):
            rows = await self._executor.fetch(
                f"SELECT {_FINDING_COLUMNS} FROM hygiene_findings WHERE {' AND '.join(clauses)}",
                tuple(params),
            )
        return [_finding(row) for row in rows]

    async def get_finding(self, tenant_id: str, finding_id: str) -> HygieneFinding | None:
        with _tracer.start_as_current_span("postgres.hygiene.get_finding"):
            rows = await self._executor.fetch(
                f"SELECT {_FINDING_COLUMNS} FROM hygiene_findings WHERE tenant_id = %s AND id = %s",
                (tenant_id, finding_id),
            )
        return _finding(rows[0]) if rows else None

    async def save_finding(self, finding: HygieneFinding) -> None:
        decision = finding.decision
        with _tracer.start_as_current_span("postgres.hygiene.save_finding"):
            await self._executor.execute(
                f"""
                INSERT INTO hygiene_findings ({_FINDING_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s)
                ON CONFLICT (tenant_id, id) DO UPDATE SET
                    rule_version = EXCLUDED.rule_version,
                    state = EXCLUDED.state,
                    owner_member_id = EXCLUDED.owner_member_id,
                    detail = EXCLUDED.detail,
                    fingerprint = EXCLUDED.fingerprint,
                    held = EXCLUDED.held,
                    last_seen_at = EXCLUDED.last_seen_at,
                    resolved_at = EXCLUDED.resolved_at,
                    resolution = EXCLUDED.resolution,
                    decided_by = EXCLUDED.decided_by,
                    decided_at = EXCLUDED.decided_at,
                    decision_reason = EXCLUDED.decision_reason
                """,
                (
                    finding.tenant_id,
                    finding.finding_id,
                    finding.rule_id,
                    finding.rule_version,
                    finding.issue_key,
                    finding.scope.kind.value,
                    finding.scope.id,
                    finding.project_id,
                    finding.state.value,
                    finding.owner_member_id,
                    json.dumps(dict(finding.detail), default=str),
                    finding.fingerprint,
                    finding.held,
                    finding.first_seen_at,
                    finding.last_seen_at,
                    finding.resolved_at,
                    finding.resolution.value if finding.resolution else None,
                    decision.by if decision else None,
                    decision.at if decision else None,
                    decision.reason if decision else None,
                ),
            )

    # ---- audit and runs ---------------------------------------------------------------

    async def append_action(self, action: HygieneAction) -> None:
        with _tracer.start_as_current_span("postgres.hygiene.append_action"):
            await self._executor.execute(
                f"""
                INSERT INTO hygiene_actions ({_ACTION_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    action.tenant_id,
                    action.action_id,
                    action.finding_id,
                    action.rule_id,
                    action.action,
                    action.actor,
                    action.at,
                    action.note,
                    json.dumps(dict(action.detail), default=str) if action.detail else None,
                ),
            )

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[HygieneAction]:
        with _tracer.start_as_current_span("postgres.hygiene.list_actions"):
            rows = await self._executor.fetch(
                f"SELECT {_ACTION_COLUMNS} FROM hygiene_actions "
                "WHERE tenant_id = %s AND finding_id = %s ORDER BY at",
                (tenant_id, finding_id),
            )
        return [_action(row) for row in rows]

    async def claim_run(self, run: HygieneRun) -> bool:
        with _tracer.start_as_current_span("postgres.hygiene.claim_run"):
            rows = await self._executor.fetch(
                f"""
                INSERT INTO hygiene_runs ({_RUN_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (tenant_id, slot) DO NOTHING
                RETURNING id
                """,
                (
                    run.tenant_id,
                    run.run_id,
                    run.slot,
                    run.trigger.value,
                    run.status.value,
                    run.started_at,
                    run.finished_at,
                    json.dumps(dict(run.counts)),
                    run.data_as_of,
                    run.error_category,
                    run.actor,
                ),
            )
        return bool(rows)

    async def finish_run(self, run: HygieneRun) -> None:
        with _tracer.start_as_current_span("postgres.hygiene.finish_run"):
            await self._executor.execute(
                """
                UPDATE hygiene_runs SET status = %s, finished_at = %s, counts = %s::jsonb,
                    data_as_of = %s, error_category = %s
                WHERE tenant_id = %s AND id = %s
                """,
                (
                    run.status.value,
                    run.finished_at,
                    json.dumps(dict(run.counts)),
                    run.data_as_of,
                    run.error_category,
                    run.tenant_id,
                    run.run_id,
                ),
            )

    async def last_run(self, tenant_id: str) -> HygieneRun | None:
        with _tracer.start_as_current_span("postgres.hygiene.last_run"):
            rows = await self._executor.fetch(
                f"SELECT {_RUN_COLUMNS} FROM hygiene_runs "
                "WHERE tenant_id = %s AND finished_at IS NOT NULL "
                "ORDER BY started_at DESC LIMIT 1",
                (tenant_id,),
            )
        return _run(rows[0]) if rows else None


# ---- JSON shapes ----------------------------------------------------------------------


def rule_spec(rule: HygieneRule) -> dict[str, object]:
    """A rule as kept in ``hygiene_rules.spec``: everything but its row's own columns."""
    select, condition = rule.select, rule.condition
    return {
        "scope": rule.scope.value,
        "select": {
            "matchers": [
                {"kind": item.kind.value, "value": item.value} for item in select.matchers
            ],
            "stage_filter": select.stage_filter.value,
            "stage": select.stage.value if select.stage else None,
            "every_type": select.every_type,
        },
        "condition": {
            "kind": condition.kind.value,
            "labels": list(condition.labels),
            "field": condition.field.value if condition.field else None,
            "gate_kind": condition.gate_kind,
            "stage": condition.stage.value if condition.stage else None,
            "working_days": condition.working_days,
            "points_over": condition.points_over,
        },
        "severity": rule.severity.value,
        "need": rule.need.value,
        "owner": {"kind": rule.owner.kind.value, "profile": rule.owner.profile},
        "blocking_before": rule.blocking_before.value if rule.blocking_before else None,
        "suggested_fix": rule.suggested_fix,
    }


def rule_from_spec(
    tenant_id: str, rule_id: str, name: str, spec: Mapping[str, object]
) -> HygieneRule:
    select = _mapping(spec.get("select"))
    condition = _mapping(spec.get("condition"))
    owner = _mapping(spec.get("owner"))
    return HygieneRule(
        tenant_id=tenant_id,
        rule_id=rule_id,
        name=name,
        scope=RuleScope(str(spec.get("scope") or RuleScope.PROJECT.value)),
        select=Select(
            matchers=tuple(
                Matcher(
                    kind=MatcherKind(str(item.get("kind"))),
                    value=str(item.get("value") or ""),
                    strength=Strength.EVIDENCE,
                )
                for item in _items(select.get("matchers"))
            ),
            stage_filter=StageFilter(str(select.get("stage_filter") or StageFilter.ANY.value)),
            stage=_stage(select.get("stage")),
            every_type=bool(select.get("every_type")),
        ),
        condition=Condition(
            kind=ConditionKind(str(condition.get("kind"))),
            labels=_strings(condition.get("labels")),
            field=IssueField(str(condition["field"])) if condition.get("field") else None,
            gate_kind=str(condition.get("gate_kind") or ""),
            stage=_stage(condition.get("stage")),
            working_days=int(str(condition.get("working_days") or 0)),
            points_over=float(str(condition.get("points_over") or 0)),
        ),
        severity=RuleSeverity(str(spec.get("severity") or RuleSeverity.LIST_ONLY.value)),
        need=RuleNeed(str(spec.get("need") or RuleNeed.FIX.value)),
        owner=Owner(
            kind=OwnerKind(str(owner.get("kind") or OwnerKind.POD_SCRUM_MASTER.value)),
            profile=str(owner.get("profile") or ""),
        ),
        blocking_before=_stage(spec.get("blocking_before")),
        suggested_fix=str(spec.get("suggested_fix") or ""),
    )


def _rule(row: Mapping[str, object]) -> HygieneRule:
    base = rule_from_spec(
        str(row["tenant_id"]), str(row["rule_id"]), str(row["name"]), _mapping(row.get("spec"))
    )
    return HygieneRule(
        tenant_id=base.tenant_id,
        rule_id=base.rule_id,
        name=base.name,
        scope=base.scope,
        select=base.select,
        condition=base.condition,
        severity=base.severity,
        need=base.need,
        owner=base.owner,
        blocking_before=base.blocking_before,
        suggested_fix=base.suggested_fix,
        enabled=bool(row.get("enabled")),
        version=int(str(row.get("version") or 1)),
        updated_at=_moment(row.get("updated_at")),
        updated_by=_optional_text(row.get("updated_by")),
        deleted_at=_moment(row.get("deleted_at")),
    )


def _finding(row: Mapping[str, object]) -> HygieneFinding:
    decided_by = _optional_text(row.get("decided_by"))
    decided_at = _moment(row.get("decided_at"))
    resolution = _optional_text(row.get("resolution"))
    return HygieneFinding(
        tenant_id=str(row["tenant_id"]),
        finding_id=str(row["id"]),
        rule_id=str(row["rule_id"]),
        rule_version=int(str(row.get("rule_version") or 1)),
        issue_key=str(row["issue_key"]),
        scope=ScopeRef(kind=ScopeKind(str(row["scope_kind"])), id=str(row["scope_id"])),
        project_id=_optional_text(row.get("project_id")),
        state=HygieneState(str(row["state"])),
        owner_member_id=_optional_text(row.get("owner_member_id")),
        detail=dict(_mapping(row.get("detail"))),
        fingerprint=str(row.get("fingerprint") or ""),
        held=bool(row.get("held")),
        first_seen_at=_required_moment(row, "first_seen_at"),
        last_seen_at=_required_moment(row, "last_seen_at"),
        resolved_at=_moment(row.get("resolved_at")),
        resolution=Resolution(resolution) if resolution else None,
        decision=(
            PersonDecision(by=decided_by, at=decided_at, reason=_text(row.get("decision_reason")))
            if decided_by and decided_at
            else None
        ),
    )


def _action(row: Mapping[str, object]) -> HygieneAction:
    detail = _json(row.get("detail"))
    return HygieneAction(
        tenant_id=str(row["tenant_id"]),
        action_id=str(row["id"]),
        at=_required_moment(row, "at"),
        actor=str(row["actor"]),
        action=str(row["action"]),
        rule_id=_optional_text(row.get("rule_id")),
        finding_id=_optional_text(row.get("finding_id")),
        note=_optional_text(row.get("note")),
        detail=detail if isinstance(detail, Mapping) else None,
    )


def _run(row: Mapping[str, object]) -> HygieneRun:
    counts = _mapping(row.get("counts"))
    return HygieneRun(
        tenant_id=str(row["tenant_id"]),
        run_id=str(row["id"]),
        slot=str(row["slot"]),
        trigger=RunTrigger(str(row["trigger"])),
        status=RunStatus(str(row["status"])),
        started_at=_required_moment(row, "started_at"),
        finished_at=_moment(row.get("finished_at")),
        counts={str(key): int(str(value)) for key, value in counts.items()},
        data_as_of=_moment(row.get("data_as_of")),
        error_category=_optional_text(row.get("error_category")),
        actor=_optional_text(row.get("actor")),
    )


def _stage(value: object) -> DeliveryStage | None:
    if isinstance(value, str) and value in {stage.value for stage in DeliveryStage}:
        return DeliveryStage(value)
    return None


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _mapping(value: object) -> Mapping[str, object]:
    loaded = _json(value)
    return loaded if isinstance(loaded, Mapping) else {}


def _items(value: object) -> list[Mapping[str, object]]:
    loaded = _json(value)
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, Mapping)]


def _strings(value: object) -> tuple[str, ...]:
    loaded = _json(value)
    return tuple(str(item) for item in loaded) if isinstance(loaded, list | tuple) else ()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _moment(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _required_moment(row: Mapping[str, object], key: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"hygiene row has no {key}")
    return value
