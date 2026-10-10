"""The release readiness tables round-trip through the Postgres repository.

Never point it at a database anyone uses: it writes readiness rows. Run it on a
copy of a seeded database migrated to 0040, under a tenant of its own:

    docker exec openprogram-postgres-1 createdb -U openprogram -T <seeded db> rrtest
    OPENPROGRAM_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/rrtest \
      PYTHONPATH=backend uv run alembic -c backend/infra/persistence/alembic.ini upgrade head
    export OPENPROGRAM_READINESS_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/rrtest
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_readiness_repository.py --no-cov
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from core.domain.release_readiness import (
    CandidateRef,
    DecidedBy,
    DecisionKind,
    Draft,
    EvidenceRef,
    Finding,
    FindingState,
    MatcherKind,
    PersonDecision,
    ReadinessAction,
    ReadinessRun,
    ReadinessSettings,
    RunStatus,
    RunTrigger,
    ScopeKind,
    ScopeRef,
    Suggestion,
    SuggestionStatus,
    Urgency,
    UrgencyKind,
    default_examples,
)
from infra.persistence.postgres_readiness import PostgresReleaseReadinessRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = os.getenv("OPENPROGRAM_READINESS_DATABASE_URL", "")
AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_READINESS_DATABASE_URL",
    ),
]


async def test_every_readiness_row_round_trips() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresReleaseReadinessRepository(executor)
    tenant = f"rr-test-{uuid4().hex[:8]}"
    try:
        settings = ReadinessSettings(
            tenant_id=tenant, enabled=True, labels=("release-readiness",), updated_at=AT
        )
        await repository.save_settings(settings)
        assert await repository.get_settings(tenant) == replace_by(settings, updated_by=None)

        example = default_examples(tenant)[1]
        saved = replace_by(example, updated_at=AT, updated_by="U1001")
        await repository.save_criterion(saved)
        assert await repository.list_criteria(tenant) == [saved]

        scope = ScopeRef(kind=ScopeKind.RELEASE, id="rel-1")
        finding = Finding(
            tenant_id=tenant,
            finding_id="rf_1",
            criterion_id=example.criterion_id,
            criterion_version=1,
            scope=scope,
            project_id="project-checkout",
            state=FindingState.UNSURE,
            done=False,
            decided_by=DecidedBy.RULES,
            evidence=(EvidenceRef(issue_key="CHK-1", title="x", how="label load-test"),),
            candidates=(CandidateRef(issue_key="CHK-13", title="y", why=MatcherKind.TITLE_WORDS),),
            reason="Only the title's words match: CHK-13.",
            urgency=Urgency(
                kind=UrgencyKind.DUE_SOON,
                due_on=date(2026, 10, 21),
                delivery_date=date(2026, 11, 4),
            ),
            held=True,
            applies=True,
            person=PersonDecision(kind=DecisionKind.LINKED, by="U1003", at=AT, issue_key="CHK-13"),
            fingerprint="f1",
            first_seen_at=AT,
            window_entered_at=AT,
            last_run_id="rr_1",
            updated_at=AT,
        )
        await repository.save_finding(finding)
        assert await repository.get_finding(tenant, "rf_1") == finding
        assert await repository.list_findings(tenant, [scope]) == [finding]

        suggestion = Suggestion(
            tenant_id=tenant,
            suggestion_id="rs_1",
            finding_id="rf_1",
            status=SuggestionStatus.OPEN,
            version=2,
            draft=Draft(
                project_key="CHK",
                issue_type="Task",
                summary="Load test for Release 1",
                description="text",
                labels=("load-test",),
            ),
            marker_label="op-rr-12345678",
            criterion_version=1,
            updated_at=AT,
        )
        await repository.save_suggestion(suggestion)
        assert await repository.list_suggestions(tenant, ["rf_1"]) == [suggestion]
        assert not await repository.claim_suggestion(tenant, "rs_1", version=1, at=AT)
        assert await repository.claim_suggestion(tenant, "rs_1", version=2, at=AT)
        assert not await repository.claim_suggestion(tenant, "rs_1", version=2, at=AT)
        claimed = await repository.get_suggestion(tenant, "rs_1")
        assert claimed is not None and claimed.status is SuggestionStatus.CREATING

        await repository.append_action(
            ReadinessAction(
                tenant_id=tenant,
                action_id="ra_1",
                at=AT,
                actor="agent",
                action="state_changed",
                finding_id="rf_1",
                after={"state": "unsure"},
            )
        )
        [action] = await repository.list_actions(tenant, "rf_1")
        assert (action.action, action.after) == ("state_changed", {"state": "unsure"})

        run = ReadinessRun(
            tenant_id=tenant,
            run_id="rr_1",
            slot="2026-10-05T09:45:00+00:00",
            trigger=RunTrigger.SCHEDULE,
            status=RunStatus.RUNNING,
            started_at=AT,
        )
        assert await repository.claim_run(run)
        assert not await repository.claim_run(replace_by(run, run_id="rr_2"))
        await repository.finish_run(
            replace_by(run, status=RunStatus.OK, finished_at=AT, counts={"scopes": 3})
        )
        last = await repository.last_run(tenant)
        assert last is not None and (last.status, last.counts) == (RunStatus.OK, {"scopes": 3})
    finally:
        for table in (
            "readiness_actions",
            "readiness_suggestions",
            "readiness_findings",
            "readiness_runs",
            "readiness_criteria",
            "readiness_settings",
        ):
            await executor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant,))
        await executor.close()


def replace_by(value: object, **changes: object) -> object:
    from dataclasses import replace

    return replace(value, **changes)  # type: ignore[type-var]
