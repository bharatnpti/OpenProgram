from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.delivery import DeliveryStage
from core.domain.release_readiness import (
    AppliesTo,
    CandidateRef,
    DecidedBy,
    DecisionKind,
    Draft,
    DraftTemplate,
    EvidenceRef,
    Finding,
    FindingState,
    Matcher,
    MatcherKind,
    PersonDecision,
    ReadinessAction,
    ReadinessRun,
    ReadinessSettings,
    ReleaseCriterion,
    RunStatus,
    RunTrigger,
    ScopeKind,
    ScopeRef,
    Severity,
    Strength,
    Suggestion,
    SuggestionStatus,
    Urgency,
    UrgencyKind,
)

_tracer = trace.get_tracer("openprogram.persistence.readiness")

_CRITERION_COLUMNS = (
    "tenant_id, criterion_id, version, name, spec, enabled, deleted_at, updated_at, updated_by"
)
_FINDING_COLUMNS = (
    "tenant_id, finding_id, criterion_id, criterion_version, scope_kind, scope_id, project_id, "
    "state, done, decided_by, evidence, candidates, reason, urgency, due_on, delivery_date, "
    "stage_key, held, applies, person_decision, person_evidence, person_reason, person_by, "
    "person_at, fingerprint, first_seen_at, window_entered_at, last_run_id, updated_at"
)
_SUGGESTION_COLUMNS = (
    "tenant_id, suggestion_id, finding_id, status, version, criterion_version, draft, "
    "edited_by, marker_label, created_issue_key, created_by, created_at, dismissed_by, "
    "dismissed_reason, dismissed_at, updated_at"
)
_ACTION_COLUMNS = (
    "tenant_id, action_id, at, actor, action, finding_id, suggestion_id, criterion_id, "
    "before, after, reason, correlation_id"
)
_RUN_COLUMNS = (
    "tenant_id, run_id, slot, trigger, scope_kind, scope_id, status, started_at, finished_at, "
    "counts, data_as_of, error_category, actor"
)


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresReleaseReadinessRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    # ---- settings and criteria -------------------------------------------------------

    async def get_settings(self, tenant_id: str) -> ReadinessSettings | None:
        """The agent's settings, None while never saved.

        The row also holds the tenant's Jira writes switches (``jira_writes``,
        see ``PostgresJiraWritesRepository``); a row holding only those is not
        a save of these settings.
        """
        with _tracer.start_as_current_span("postgres.readiness.get_settings"):
            rows = await self._executor.fetch(
                "SELECT tenant_id, settings, updated_at, updated_by FROM readiness_settings "
                "WHERE tenant_id = %s",
                (tenant_id,),
            )
        if not rows or "enabled" not in _mapping(rows[0].get("settings")):
            return None
        return _settings(rows[0])

    async def save_settings(self, settings: ReadinessSettings) -> None:
        # ``jira_writes`` belongs to the Jira writes panel: a save here keeps it as it is.
        with _tracer.start_as_current_span("postgres.readiness.save_settings"):
            await self._executor.execute(
                """
                INSERT INTO readiness_settings (tenant_id, settings, updated_at, updated_by)
                VALUES (%s, %s::jsonb, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    settings = CASE
                        WHEN readiness_settings.settings -> 'jira_writes' IS NULL
                            THEN EXCLUDED.settings
                        ELSE EXCLUDED.settings || jsonb_build_object(
                            'jira_writes', readiness_settings.settings -> 'jira_writes'
                        )
                    END,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    settings.tenant_id,
                    json.dumps(
                        {
                            "enabled": settings.enabled,
                            "auto_suggest": settings.auto_suggest,
                            "create_in_jira": settings.create_in_jira,
                            "issue_type": settings.issue_type,
                            "labels": list(settings.labels),
                        }
                    ),
                    settings.updated_at,
                    settings.updated_by or "",
                ),
            )

    async def list_criteria(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[ReleaseCriterion]:
        deleted = "" if include_deleted else " AND deleted_at IS NULL"
        with _tracer.start_as_current_span("postgres.readiness.list_criteria"):
            rows = await self._executor.fetch(
                f"SELECT {_CRITERION_COLUMNS} FROM readiness_criteria "
                f"WHERE tenant_id = %s{deleted} ORDER BY lower(name)",
                (tenant_id,),
            )
        return [_criterion(row) for row in rows]

    async def save_criterion(self, criterion: ReleaseCriterion) -> None:
        with _tracer.start_as_current_span("postgres.readiness.save_criterion"):
            await self._executor.execute(
                f"""
                INSERT INTO readiness_criteria ({_CRITERION_COLUMNS})
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, criterion_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    name = EXCLUDED.name,
                    spec = EXCLUDED.spec,
                    enabled = EXCLUDED.enabled,
                    deleted_at = EXCLUDED.deleted_at,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    criterion.tenant_id,
                    criterion.criterion_id,
                    criterion.version,
                    criterion.name,
                    json.dumps(criterion_spec(criterion)),
                    criterion.enabled,
                    criterion.deleted_at,
                    criterion.updated_at,
                    criterion.updated_by or "",
                ),
            )

    # ---- findings --------------------------------------------------------------------

    async def list_findings(self, tenant_id: str, scopes: Sequence[ScopeRef]) -> list[Finding]:
        if not scopes:
            return []
        with _tracer.start_as_current_span("postgres.readiness.list_findings"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_FINDING_COLUMNS} FROM readiness_findings
                WHERE tenant_id = %s AND (scope_kind || ':' || scope_id) = ANY(%s)
                """,
                (tenant_id, [scope.key for scope in scopes]),
            )
        return [_finding(row) for row in rows]

    async def get_finding(self, tenant_id: str, finding_id: str) -> Finding | None:
        with _tracer.start_as_current_span("postgres.readiness.get_finding"):
            rows = await self._executor.fetch(
                f"SELECT {_FINDING_COLUMNS} FROM readiness_findings "
                "WHERE tenant_id = %s AND finding_id = %s",
                (tenant_id, finding_id),
            )
        return _finding(rows[0]) if rows else None

    async def save_finding(self, finding: Finding) -> None:
        person = finding.person
        with _tracer.start_as_current_span("postgres.readiness.save_finding"):
            await self._executor.execute(
                f"""
                INSERT INTO readiness_findings ({_FINDING_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, finding_id) DO UPDATE SET
                    criterion_version = EXCLUDED.criterion_version,
                    project_id = EXCLUDED.project_id,
                    state = EXCLUDED.state,
                    done = EXCLUDED.done,
                    decided_by = EXCLUDED.decided_by,
                    evidence = EXCLUDED.evidence,
                    candidates = EXCLUDED.candidates,
                    reason = EXCLUDED.reason,
                    urgency = EXCLUDED.urgency,
                    due_on = EXCLUDED.due_on,
                    delivery_date = EXCLUDED.delivery_date,
                    stage_key = EXCLUDED.stage_key,
                    held = EXCLUDED.held,
                    applies = EXCLUDED.applies,
                    person_decision = EXCLUDED.person_decision,
                    person_evidence = EXCLUDED.person_evidence,
                    person_reason = EXCLUDED.person_reason,
                    person_by = EXCLUDED.person_by,
                    person_at = EXCLUDED.person_at,
                    fingerprint = EXCLUDED.fingerprint,
                    window_entered_at = EXCLUDED.window_entered_at,
                    last_run_id = EXCLUDED.last_run_id,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    finding.tenant_id,
                    finding.finding_id,
                    finding.criterion_id,
                    finding.criterion_version,
                    finding.scope.kind.value,
                    finding.scope.id,
                    finding.project_id,
                    finding.state.value,
                    finding.done,
                    finding.decided_by.value,
                    json.dumps([evidence_json(item) for item in finding.evidence]),
                    json.dumps([candidate_json(item) for item in finding.candidates]),
                    finding.reason,
                    finding.urgency.kind.value,
                    finding.urgency.due_on,
                    finding.urgency.delivery_date,
                    finding.urgency.stage_key,
                    finding.held,
                    finding.applies,
                    person.kind.value if person else None,
                    json.dumps(
                        {
                            "issue_key": person.issue_key,
                            "url": person.url,
                            "note": person.note,
                            "created": person.created,
                        }
                    )
                    if person
                    else None,
                    person.reason if person else None,
                    person.by if person else None,
                    person.at if person else None,
                    finding.fingerprint,
                    finding.first_seen_at,
                    finding.window_entered_at,
                    finding.last_run_id,
                    finding.updated_at,
                ),
            )

    # ---- suggestions -----------------------------------------------------------------

    async def list_suggestions(
        self, tenant_id: str, finding_ids: Sequence[str]
    ) -> list[Suggestion]:
        if not finding_ids:
            return []
        with _tracer.start_as_current_span("postgres.readiness.list_suggestions"):
            rows = await self._executor.fetch(
                f"SELECT {_SUGGESTION_COLUMNS} FROM readiness_suggestions "
                "WHERE tenant_id = %s AND finding_id = ANY(%s)",
                (tenant_id, list(finding_ids)),
            )
        return [_suggestion(row) for row in rows]

    async def get_suggestion(self, tenant_id: str, suggestion_id: str) -> Suggestion | None:
        with _tracer.start_as_current_span("postgres.readiness.get_suggestion"):
            rows = await self._executor.fetch(
                f"SELECT {_SUGGESTION_COLUMNS} FROM readiness_suggestions "
                "WHERE tenant_id = %s AND suggestion_id = %s",
                (tenant_id, suggestion_id),
            )
        return _suggestion(rows[0]) if rows else None

    async def save_suggestion(self, suggestion: Suggestion) -> None:
        with _tracer.start_as_current_span("postgres.readiness.save_suggestion"):
            await self._executor.execute(
                f"""
                INSERT INTO readiness_suggestions ({_SUGGESTION_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, suggestion_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    version = EXCLUDED.version,
                    criterion_version = EXCLUDED.criterion_version,
                    draft = EXCLUDED.draft,
                    edited_by = EXCLUDED.edited_by,
                    created_issue_key = EXCLUDED.created_issue_key,
                    created_by = EXCLUDED.created_by,
                    created_at = EXCLUDED.created_at,
                    dismissed_by = EXCLUDED.dismissed_by,
                    dismissed_reason = EXCLUDED.dismissed_reason,
                    dismissed_at = EXCLUDED.dismissed_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    suggestion.tenant_id,
                    suggestion.suggestion_id,
                    suggestion.finding_id,
                    suggestion.status.value,
                    suggestion.version,
                    suggestion.criterion_version,
                    json.dumps(draft_json(suggestion.draft)),
                    suggestion.edited_by,
                    suggestion.marker_label,
                    suggestion.created_issue_key,
                    suggestion.created_by,
                    suggestion.created_at,
                    suggestion.dismissed_by,
                    suggestion.dismissed_reason,
                    suggestion.dismissed_at,
                    suggestion.updated_at,
                ),
            )

    async def claim_suggestion(
        self, tenant_id: str, suggestion_id: str, *, version: int, at: datetime
    ) -> bool:
        with _tracer.start_as_current_span("postgres.readiness.claim_suggestion"):
            rows = await self._executor.fetch(
                """
                UPDATE readiness_suggestions SET status = 'creating', updated_at = %s
                WHERE tenant_id = %s AND suggestion_id = %s AND status = 'open' AND version = %s
                RETURNING suggestion_id
                """,
                (at, tenant_id, suggestion_id, version),
            )
        return bool(rows)

    # ---- audit and runs --------------------------------------------------------------

    async def append_action(self, action: ReadinessAction) -> None:
        with _tracer.start_as_current_span("postgres.readiness.append_action"):
            await self._executor.execute(
                f"""
                INSERT INTO readiness_actions ({_ACTION_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    action.tenant_id,
                    action.action_id,
                    action.at,
                    action.actor,
                    action.action,
                    action.finding_id,
                    action.suggestion_id,
                    action.criterion_id,
                    json.dumps(dict(action.before), default=str) if action.before else None,
                    json.dumps(dict(action.after), default=str) if action.after else None,
                    action.reason,
                    action.correlation_id,
                ),
            )

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[ReadinessAction]:
        with _tracer.start_as_current_span("postgres.readiness.list_actions"):
            rows = await self._executor.fetch(
                f"SELECT {_ACTION_COLUMNS} FROM readiness_actions "
                "WHERE tenant_id = %s AND finding_id = %s ORDER BY at, action_id",
                (tenant_id, finding_id),
            )
        return [_action(row) for row in rows]

    async def claim_run(self, run: ReadinessRun) -> bool:
        with _tracer.start_as_current_span("postgres.readiness.claim_run"):
            rows = await self._executor.fetch(
                f"""
                INSERT INTO readiness_runs ({_RUN_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (tenant_id, slot) DO NOTHING
                RETURNING run_id
                """,
                _run_params(run),
            )
        return bool(rows)

    async def finish_run(self, run: ReadinessRun) -> None:
        with _tracer.start_as_current_span("postgres.readiness.finish_run"):
            await self._executor.execute(
                """
                UPDATE readiness_runs SET status = %s, finished_at = %s, counts = %s::jsonb,
                    data_as_of = %s, error_category = %s
                WHERE tenant_id = %s AND run_id = %s
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

    async def last_run(self, tenant_id: str) -> ReadinessRun | None:
        with _tracer.start_as_current_span("postgres.readiness.last_run"):
            rows = await self._executor.fetch(
                f"SELECT {_RUN_COLUMNS} FROM readiness_runs "
                "WHERE tenant_id = %s AND finished_at IS NOT NULL "
                "ORDER BY started_at DESC LIMIT 1",
                (tenant_id,),
            )
        return _run(rows[0]) if rows else None


# ---- JSON shapes ----------------------------------------------------------------------


def criterion_spec(criterion: ReleaseCriterion) -> dict[str, object]:
    return {
        "evidence": criterion.evidence,
        "applies_to": criterion.applies_to.value,
        "required_before": criterion.required_before.value,
        "lead_working_days": criterion.lead_working_days,
        "severity": criterion.severity.value,
        "needs_done": criterion.needs_done,
        "when_labels": list(criterion.when_labels),
        "when_types": list(criterion.when_types),
        "matchers": [
            {"kind": item.kind.value, "value": item.value, "strength": item.strength.value}
            for item in criterion.matchers
        ],
        "draft": {
            "project_key": criterion.draft.project_key,
            "issue_type": criterion.draft.issue_type,
            "summary": criterion.draft.summary,
            "description": criterion.draft.description,
            "labels": list(criterion.draft.labels),
        },
    }


def evidence_json(item: EvidenceRef) -> dict[str, object]:
    return {
        "issue_key": item.issue_key,
        "title": item.title,
        "status": item.status,
        "done": item.done,
        "how": item.how,
        "url": item.url,
        "note": item.note,
    }


def candidate_json(item: CandidateRef) -> dict[str, object]:
    return {"issue_key": item.issue_key, "title": item.title, "why": item.why.value}


def draft_json(draft: Draft) -> dict[str, object]:
    return {
        "project_key": draft.project_key,
        "issue_type": draft.issue_type,
        "summary": draft.summary,
        "description": draft.description,
        "labels": list(draft.labels),
    }


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
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _moment(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _settings(row: Mapping[str, object]) -> ReadinessSettings:
    spec = _mapping(row.get("settings"))
    return ReadinessSettings(
        tenant_id=str(row["tenant_id"]),
        enabled=bool(spec.get("enabled", False)),
        auto_suggest=bool(spec.get("auto_suggest", True)),
        create_in_jira=bool(spec.get("create_in_jira", False)),
        issue_type=_text(spec.get("issue_type")) or "Task",
        labels=_strings(spec.get("labels")),
        updated_at=_moment(row.get("updated_at")),
        updated_by=_optional_text(row.get("updated_by")),
    )


def _criterion(row: Mapping[str, object]) -> ReleaseCriterion:
    spec = _mapping(row.get("spec"))
    draft = _mapping(spec.get("draft"))
    matchers: list[Matcher] = []
    for item in _items(spec.get("matchers")):
        try:
            matchers.append(
                Matcher(
                    kind=MatcherKind(str(item.get("kind"))),
                    value=_text(item.get("value")),
                    strength=Strength(str(item.get("strength"))),
                )
            )
        except ValueError:
            continue
    lead = spec.get("lead_working_days")
    return ReleaseCriterion(
        tenant_id=str(row["tenant_id"]),
        criterion_id=str(row["criterion_id"]),
        version=int(str(row.get("version") or 1)),
        name=str(row["name"]),
        evidence=_text(spec.get("evidence")),
        applies_to=AppliesTo(str(spec.get("applies_to") or "release")),
        required_before=DeliveryStage(str(spec.get("required_before") or "production")),
        lead_working_days=lead if isinstance(lead, int) else 10,
        severity=Severity(str(spec.get("severity") or "blocking")),
        needs_done=bool(spec.get("needs_done", True)),
        when_labels=_strings(spec.get("when_labels")),
        when_types=_strings(spec.get("when_types")),
        matchers=tuple(matchers),
        draft=DraftTemplate(
            project_key=_text(draft.get("project_key")),
            issue_type=_text(draft.get("issue_type")),
            summary=_text(draft.get("summary")) or "{criterion} for {scope}",
            description=_text(draft.get("description")),
            labels=_strings(draft.get("labels")),
        ),
        enabled=bool(row.get("enabled", True)),
        updated_at=_moment(row.get("updated_at")),
        updated_by=_optional_text(row.get("updated_by")),
        deleted_at=_moment(row.get("deleted_at")),
    )


def _finding(row: Mapping[str, object]) -> Finding:
    person_kind = row.get("person_decision")
    person: PersonDecision | None = None
    if isinstance(person_kind, str) and person_kind:
        evidence = _mapping(row.get("person_evidence"))
        person = PersonDecision(
            kind=DecisionKind(person_kind),
            by=_text(row.get("person_by")),
            at=_moment(row.get("person_at")) or _required_moment(row, "updated_at"),
            reason=_text(row.get("person_reason")),
            issue_key=_text(evidence.get("issue_key")),
            url=_text(evidence.get("url")),
            note=_text(evidence.get("note")),
            created=bool(evidence.get("created")),
        )
    candidates: list[CandidateRef] = []
    for item in _items(row.get("candidates")):
        try:
            why = MatcherKind(str(item.get("why")))
        except ValueError:
            why = MatcherKind.TITLE_WORDS
        candidates.append(
            CandidateRef(
                issue_key=_text(item.get("issue_key")), title=_text(item.get("title")), why=why
            )
        )
    return Finding(
        tenant_id=str(row["tenant_id"]),
        finding_id=str(row["finding_id"]),
        criterion_id=str(row["criterion_id"]),
        criterion_version=int(str(row.get("criterion_version") or 1)),
        scope=ScopeRef(kind=ScopeKind(str(row["scope_kind"])), id=str(row["scope_id"])),
        project_id=_optional_text(row.get("project_id")),
        state=FindingState(str(row["state"])),
        done=bool(row.get("done")),
        decided_by=DecidedBy(str(row.get("decided_by") or "rules")),
        evidence=tuple(
            EvidenceRef(
                issue_key=_text(item.get("issue_key")),
                title=_text(item.get("title")),
                status=_text(item.get("status")),
                done=bool(item.get("done")),
                how=_text(item.get("how")),
                url=_text(item.get("url")),
                note=_text(item.get("note")),
            )
            for item in _items(row.get("evidence"))
        ),
        candidates=tuple(candidates),
        reason=_text(row.get("reason")),
        urgency=Urgency(
            kind=UrgencyKind(str(row.get("urgency") or "later")),
            due_on=_day(row.get("due_on")),
            delivery_date=_day(row.get("delivery_date")),
            stage_key=_optional_text(row.get("stage_key")),
        ),
        held=bool(row.get("held")),
        applies=bool(row.get("applies", True)),
        person=person,
        fingerprint=_text(row.get("fingerprint")),
        first_seen_at=_required_moment(row, "first_seen_at"),
        window_entered_at=_moment(row.get("window_entered_at")),
        last_run_id=_optional_text(row.get("last_run_id")),
        updated_at=_required_moment(row, "updated_at"),
    )


def _suggestion(row: Mapping[str, object]) -> Suggestion:
    draft = _mapping(row.get("draft"))
    return Suggestion(
        tenant_id=str(row["tenant_id"]),
        suggestion_id=str(row["suggestion_id"]),
        finding_id=str(row["finding_id"]),
        status=SuggestionStatus(str(row["status"])),
        version=int(str(row.get("version") or 1)),
        criterion_version=int(str(row.get("criterion_version") or 1)),
        draft=Draft(
            project_key=_text(draft.get("project_key")),
            issue_type=_text(draft.get("issue_type")),
            summary=_text(draft.get("summary")),
            description=_text(draft.get("description")),
            labels=_strings(draft.get("labels")),
        ),
        marker_label=str(row["marker_label"]),
        updated_at=_required_moment(row, "updated_at"),
        edited_by=_optional_text(row.get("edited_by")),
        created_issue_key=_optional_text(row.get("created_issue_key")),
        created_by=_optional_text(row.get("created_by")),
        created_at=_moment(row.get("created_at")),
        dismissed_by=_optional_text(row.get("dismissed_by")),
        dismissed_reason=_optional_text(row.get("dismissed_reason")),
        dismissed_at=_moment(row.get("dismissed_at")),
    )


def _action(row: Mapping[str, object]) -> ReadinessAction:
    before = _json(row.get("before"))
    after = _json(row.get("after"))
    return ReadinessAction(
        tenant_id=str(row["tenant_id"]),
        action_id=str(row["action_id"]),
        at=_required_moment(row, "at"),
        actor=str(row["actor"]),
        action=str(row["action"]),
        finding_id=_optional_text(row.get("finding_id")),
        suggestion_id=_optional_text(row.get("suggestion_id")),
        criterion_id=_optional_text(row.get("criterion_id")),
        before=before if isinstance(before, Mapping) else None,
        after=after if isinstance(after, Mapping) else None,
        reason=_optional_text(row.get("reason")),
        correlation_id=_optional_text(row.get("correlation_id")),
    )


def _run(row: Mapping[str, object]) -> ReadinessRun:
    counts = _mapping(row.get("counts"))
    scope_kind = row.get("scope_kind")
    return ReadinessRun(
        tenant_id=str(row["tenant_id"]),
        run_id=str(row["run_id"]),
        slot=str(row["slot"]),
        trigger=RunTrigger(str(row["trigger"])),
        status=RunStatus(str(row["status"])),
        started_at=_required_moment(row, "started_at"),
        scope=(
            ScopeRef(kind=ScopeKind(scope_kind), id=str(row.get("scope_id") or ""))
            if isinstance(scope_kind, str) and scope_kind
            else None
        ),
        finished_at=_moment(row.get("finished_at")),
        counts={str(key): int(str(value)) for key, value in counts.items()},
        data_as_of=_moment(row.get("data_as_of")),
        error_category=_optional_text(row.get("error_category")),
        actor=_optional_text(row.get("actor")),
    )


def _run_params(run: ReadinessRun) -> tuple[object, ...]:
    return (
        run.tenant_id,
        run.run_id,
        run.slot,
        run.trigger.value,
        run.scope.kind.value if run.scope else None,
        run.scope.id if run.scope else None,
        run.status.value,
        run.started_at,
        run.finished_at,
        json.dumps(dict(run.counts)),
        run.data_as_of,
        run.error_category,
        run.actor,
    )


def _required_moment(row: Mapping[str, object], key: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"readiness row has no {key}")
    return value
