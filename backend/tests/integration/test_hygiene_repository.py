"""Jira hygiene's settings, rules, findings, runs and audit round-trip through Postgres.

Never point it at a database anyone uses: it writes rows. Run it on a copy of a
seeded database migrated to 0043, under a tenant of its own:

    docker exec openprogram-postgres-1 createdb -U openprogram -T <seeded db> hytest
    OPENPROGRAM_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/hytest \
      PYTHONPATH=backend uv run alembic -c backend/infra/persistence/alembic.ini upgrade head
    export OPENPROGRAM_HYGIENE_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/hytest
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_hygiene_repository.py --no-cov
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.domain.delivery import DeliveryStage
from core.domain.hygiene import (
    HygieneAction,
    HygieneFinding,
    HygieneRun,
    HygieneSettings,
    HygieneState,
    PersonDecision,
    Resolution,
    RuleSeverity,
    default_examples,
)
from core.domain.release_readiness import RunStatus, RunTrigger, ScopeKind, ScopeRef
from infra.persistence.postgres_hygiene import PostgresHygieneRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = os.getenv("OPENPROGRAM_HYGIENE_DATABASE_URL", "")
AT = datetime(2026, 10, 14, 9, 45, tzinfo=UTC)
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, id="project-checkout")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_HYGIENE_DATABASE_URL",
    ),
]

_TABLES = (
    "hygiene_actions",
    "hygiene_findings",
    "hygiene_runs",
    "hygiene_rules",
    "hygiene_settings",
)


async def test_everything_round_trips_and_one_finding_is_open_per_rule_issue_and_scope() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresHygieneRepository(executor)
    tenant = f"hy-test-{uuid4().hex[:8]}"
    try:
        settings = HygieneSettings(
            tenant_id=tenant,
            enabled=True,
            change_labels=("CR", "agreed"),
            updated_by="U1",
            updated_at=AT,
        )
        await repository.save_settings(settings)
        assert await repository.get_settings(tenant) == settings

        rules = [
            replace(rule, severity=RuleSeverity.ASK, updated_at=AT, updated_by="U1")
            for rule in default_examples(tenant)
        ]
        for rule in rules:
            await repository.save_rule(rule)
        read = {rule.rule_id: rule for rule in await repository.list_rules(tenant)}
        assert read == {rule.rule_id: rule for rule in rules}

        finding = HygieneFinding(
            tenant_id=tenant,
            finding_id="hf-1",
            rule_id=rules[0].rule_id,
            rule_version=1,
            issue_key="CHK-20",
            scope=PROJECT,
            project_id="project-checkout",
            state=HygieneState.OPEN,
            owner_member_id="U1003",
            detail={
                "what": "Added after planning",
                "stage": DeliveryStage.RAISED.value,
                "pod_ids": ["pod-a"],
            },
            fingerprint="f1",
            held=True,
            first_seen_at=AT,
            last_seen_at=AT,
        )
        await repository.save_finding(finding)
        assert await repository.get_finding(tenant, "hf-1") == finding
        with pytest.raises(Exception):  # noqa: B017 - the partial unique index refuses it
            await repository.save_finding(replace(finding, finding_id="hf-2"))

        waived = replace(
            finding,
            state=HygieneState.WAIVED,
            decision=PersonDecision(by="U1003", at=AT, reason="Spike work"),
        )
        await repository.save_finding(waived)
        # Waived, it no longer holds the open slot: the same breach may open again later.
        reopened = replace(finding, finding_id="hf-2", held=False)
        await repository.save_finding(reopened)
        resolved = replace(
            reopened,
            state=HygieneState.RESOLVED,
            resolution=Resolution.LEFT_SCOPE,
            resolved_at=AT + timedelta(hours=1),
        )
        await repository.save_finding(resolved)
        shown = await repository.list_findings(
            tenant, [PROJECT], states=(HygieneState.OPEN, HygieneState.WAIVED)
        )
        assert shown == [waived]
        assert await repository.get_finding(tenant, "hf-2") == resolved
        assert await repository.list_findings(tenant, []) == []

        await repository.append_action(
            HygieneAction(
                tenant_id=tenant,
                action_id="ha-1",
                at=AT,
                actor="U1003",
                action="waived",
                rule_id=rules[0].rule_id,
                finding_id="hf-1",
                note="Spike work",
                detail={"key": "CHK-20"},
            )
        )
        [action] = await repository.list_actions(tenant, "hf-1")
        assert (action.action, action.note, action.detail) == (
            "waived",
            "Spike work",
            {"key": "CHK-20"},
        )

        run = HygieneRun(
            tenant_id=tenant,
            run_id="hr-1",
            slot="2026-10-14T09:45:00+00:00",
            trigger=RunTrigger.SCHEDULE,
            status=RunStatus.RUNNING,
            started_at=AT,
        )
        assert await repository.claim_run(run) is True
        assert await repository.claim_run(replace(run, run_id="hr-2")) is False
        assert await repository.last_run(tenant) is None
        finished = replace(
            run, status=RunStatus.OK, finished_at=AT, counts={"open": 1}, data_as_of=AT
        )
        await repository.finish_run(finished)
        assert await repository.last_run(tenant) == finished
    finally:
        for table in _TABLES:
            await executor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant,))
        await executor.close()
