"""Planning baselines and the forecast kept per day round-trip through Postgres.

Never point it at a database anyone uses: it writes rows, and the migration test takes
0044 down and up again. Run it on a copy of a seeded database migrated to head (0044),
under a tenant of its own:

    docker exec openprogram-postgres-1 createdb -U openprogram -T <seeded db> bltest
    OPENPROGRAM_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/bltest \
      PYTHONPATH=backend uv run alembic -c backend/infra/persistence/alembic.ini upgrade head
    export OPENPROGRAM_BASELINES_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/bltest
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_baselines_repository.py --no-cov
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg.errors
import pytest
from alembic import command
from alembic.config import Config

from config.settings import get_settings
from core.domain.baseline import (
    BaselineItem,
    BaselineKind,
    BaselineScopeKind,
    PlanningBaseline,
)
from core.domain.delivery import DeliveryStage
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    ForecastDayRecord,
    ForecastDaysNotKept,
    Verdict,
)
from infra.persistence.postgres_baselines import PostgresPlanningBaselineRepository
from infra.persistence.postgres_forecast import PostgresForecastDayRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = os.getenv("OPENPROGRAM_BASELINES_DATABASE_URL", "")
AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_BASELINES_DATABASE_URL",
    ),
]


async def test_a_new_baseline_replaces_the_active_one_and_both_round_trip() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresPlanningBaselineRepository(executor)
    tenant = f"bl-test-{uuid4().hex[:8]}"
    first = PlanningBaseline(
        tenant_id=tenant,
        baseline_id=uuid4().hex,
        scope_kind=BaselineScopeKind.PROJECT,
        scope_id="project-checkout",
        project_id="project-checkout",
        kind=BaselineKind.DATE,
        planned_on=date(2026, 10, 1),
        items={
            "CHK-1": BaselineItem(
                stage=DeliveryStage.GROOMED, points=3.0, due=date(2026, 11, 2), type="Story"
            ),
            "CHK-2": BaselineItem(stage=None),
        },
        reconstructed=True,
        snapshot_day=date(2026, 9, 30),
        change_label="CR",
        grace_working_days=2,
        set_by="U1003",
        set_at=AT,
        note="first",
    )
    second = replace(
        first,
        baseline_id=uuid4().hex,
        kind=BaselineKind.SNAPSHOT,
        planned_on=date(2026, 10, 5),
        reconstructed=False,
        snapshot_day=None,
        set_at=AT + timedelta(hours=1),
        note="second",
    )
    try:
        assert await repository.add(first) == first
        assert await repository.add(second) == second
        listed = await repository.list_for_scope(
            tenant, BaselineScopeKind.PROJECT, "project-checkout"
        )
        assert listed == [replace(first, superseded_at=second.set_at), second]
        assert await repository.list_for_project(tenant, "project-checkout") == listed
        assert await repository.list_for_scope(tenant, BaselineScopeKind.RELEASE, "x") == []
    finally:
        await executor.execute("DELETE FROM planning_baselines WHERE tenant_id = %s", (tenant,))
        await executor.close()


async def test_a_reconstructed_day_never_replaces_one_the_hourly_run_kept() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresForecastDayRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    scope = CommitmentScope(
        kind=CommitmentScopeKind.RELEASE, id="rel-1", project_id="project-checkout"
    )
    kept = ForecastDayRecord(
        tenant_id=tenant,
        scope=scope,
        day=date(2026, 10, 2),
        p50=date(2026, 11, 3),
        p85=date(2026, 11, 17),
        verdict=Verdict.AT_RISK,
        target_date=date(2026, 11, 10),
        team_latest=date(2026, 11, 12),
        team_latest_key="CHK-4",
        remaining=4.5,
        unit="story points",
        sample_days=12,
        completed_in_sample=6.0,
        min_sample_days=10,
        computed_at=AT,
    )
    rebuilt = replace(kept, p50=None, p85=None, verdict=Verdict.NOT_ENOUGH_DATA, reconstructed=True)
    other_day = replace(rebuilt, day=date(2026, 10, 1), attribution={"pace": 1})
    try:
        await repository.save([kept])
        await repository.save([rebuilt, other_day])
        rows = await repository.list_between(tenant, scope, date(2026, 9, 1), date(2026, 10, 31))
        assert rows == [other_day, kept]
        # A later run on the day replaces its own row.
        moved = replace(kept, p85=date(2026, 11, 24), computed_at=AT + timedelta(hours=1))
        await repository.save([moved])
        assert await repository.list_between(
            tenant, scope, date(2026, 10, 2), date(2026, 10, 2)
        ) == [moved]
    finally:
        await executor.execute("DELETE FROM forecast_days WHERE tenant_id = %s", (tenant,))
        await executor.close()


async def test_a_kept_day_gains_its_breakdown_and_nothing_else_changes() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresForecastDayRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    scope = CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-1", project_id="project-checkout")
    kept = ForecastDayRecord(
        tenant_id=tenant,
        scope=scope,
        day=date(2026, 10, 2),
        p50=date(2026, 11, 3),
        p85=date(2026, 11, 17),
        verdict=Verdict.AT_RISK,
        target_date=date(2026, 11, 10),
        team_latest=None,
        team_latest_key=None,
        remaining=4,
        unit="requirements",
        sample_days=12,
        completed_in_sample=6.0,
        min_sample_days=10,
        computed_at=AT,
    )
    breakdown = {"version": 1, "from_day": "2026-10-01", "to_day": "2026-10-02", "steps": []}
    try:
        await repository.save([kept])
        # A breakdown computed later, for a row with other dates: only the breakdown lands.
        await repository.fill_attribution(
            [replace(kept, p85=date(2027, 1, 1), attribution=breakdown)]
        )
        (row,) = await repository.list_between(tenant, scope, kept.day, kept.day)
        assert row == replace(kept, attribution=breakdown)
        # A row that has one keeps it.
        await repository.fill_attribution([replace(kept, attribution={"version": 2})])
        (again,) = await repository.list_between(tenant, scope, kept.day, kept.day)
        assert again.attribution == breakdown
    finally:
        await executor.execute("DELETE FROM forecast_days WHERE tenant_id = %s", (tenant,))
        await executor.close()


def _pod_day(tenant: str, project_id: str, day: date, p50: date | None) -> ForecastDayRecord:
    return ForecastDayRecord(
        tenant_id=tenant,
        scope=CommitmentScope(
            kind=CommitmentScopeKind.POD, id="pod-platform", project_id=project_id
        ),
        day=day,
        p50=p50,
        p85=None,
        verdict=Verdict.NOT_ENOUGH_DATA,
        target_date=None,
        team_latest=None,
        team_latest_key=None,
        remaining=3,
        unit="requirements",
        sample_days=4,
        completed_in_sample=1.0,
        min_sample_days=10,
        computed_at=AT,
    )


async def test_a_pod_two_projects_share_keeps_a_row_under_each_and_a_repeat_keeps_the_last() -> (
    None
):
    """The key has the project (0044): a pod two projects share is two keys a day. One key
    twice in one multi-row statement is still refused whole (CardinalityViolation)."""
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresForecastDayRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    day = date(2026, 10, 13)
    checkout = CommitmentScope(
        kind=CommitmentScopeKind.PROJECT, id="project-checkout", project_id="project-checkout"
    )
    identity = CommitmentScope(
        kind=CommitmentScopeKind.PROJECT, id="project-identity", project_id="project-identity"
    )
    project_rows = [
        replace(_pod_day(tenant, "project-checkout", day, date(2026, 11, 3)), scope=checkout),
        replace(_pod_day(tenant, "project-identity", day, date(2026, 11, 9)), scope=identity),
    ]
    try:
        # The database itself refuses a statement that offers a key twice, and saves none.
        columns = (
            "tenant_id, scope_kind, scope_id, project_id, day, verdict, remaining, unit, "
            "sample_days, completed_in_sample, min_sample_days, computed_at"
        )
        row = (tenant, "pod", "pod-platform", "project-checkout", day, "no_date", 1, "requirements")
        row += (1, 1, 1, AT)
        with pytest.raises(psycopg.errors.CardinalityViolation):
            await executor.execute(
                f"""
                INSERT INTO forecast_days ({columns})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, scope_kind, scope_id, project_id, day)
                DO UPDATE SET p50 = EXCLUDED.p50
                """,
                (*row, *row),
            )
        refused = await executor.fetch(
            "SELECT count(*) AS rows FROM forecast_days WHERE tenant_id = %s", (tenant,)
        )
        assert refused[0]["rows"] == 0

        # The repository's save of a day: the pod under each project, both projects, and the
        # pod's part of Identity offered twice, of which the last stays.
        await repository.save(
            [
                project_rows[0],
                _pod_day(tenant, "project-checkout", day, date(2026, 11, 3)),
                _pod_day(tenant, "project-identity", day, date(2026, 11, 20)),
                _pod_day(tenant, "project-identity", day, date(2026, 12, 1)),
                project_rows[1],
            ]
        )
        found = await executor.fetch(
            "SELECT scope_kind, scope_id, project_id, p50 FROM forecast_days "
            "WHERE tenant_id = %s ORDER BY scope_kind, scope_id, project_id",
            (tenant,),
        )
        assert [(r["scope_kind"], r["scope_id"], r["project_id"], r["p50"]) for r in found] == [
            ("pod", "pod-platform", "project-checkout", date(2026, 11, 3)),
            ("pod", "pod-platform", "project-identity", date(2026, 12, 1)),
            ("project", "project-checkout", "project-checkout", date(2026, 11, 3)),
            ("project", "project-identity", "project-identity", date(2026, 11, 9)),
        ]
        # Each project reads its own part of the pod, never the other's.
        under_identity, under_checkout = (
            CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-platform", project_id=project)
            for project in ("project-identity", "project-checkout")
        )
        assert [
            row.p50 for row in await repository.list_between(tenant, under_identity, day, day)
        ] == [date(2026, 12, 1)]
        assert [
            row.p50 for row in await repository.list_between(tenant, under_checkout, day, day)
        ] == [date(2026, 11, 3)]
        # A rebuilt row of one project's part never replaces the kept one; the other's is
        # replaced by a later run of its own.
        await repository.save(
            [
                replace(
                    _pod_day(tenant, "project-checkout", day, date(2027, 3, 1)), reconstructed=True
                ),
                _pod_day(tenant, "project-identity", day, date(2026, 12, 8)),
            ]
        )
        assert [
            row.p50 for row in await repository.list_between(tenant, under_checkout, day, day)
        ] == [date(2026, 11, 3)]
        assert [
            row.p50 for row in await repository.list_between(tenant, under_identity, day, day)
        ] == [date(2026, 12, 8)]

        # A later run replaces the rows, and a repeat across the 100-row batch edge is still one.
        many = [
            replace(
                _pod_day(tenant, "project-checkout", day, date(2026, 11, 4)),
                scope=CommitmentScope(
                    kind=CommitmentScopeKind.POD,
                    id=f"pod-{index:03d}",
                    project_id="project-checkout",
                ),
            )
            for index in range(150)
        ]
        repeat = replace(many[0], p50=date(2027, 1, 4))
        await repository.save([*many, repeat])
        total = await executor.fetch(
            "SELECT count(*) AS rows FROM forecast_days WHERE tenant_id = %s", (tenant,)
        )
        first = await repository.list_between(tenant, many[0].scope, day, day)
        assert total[0]["rows"] == 154
        assert [row.p50 for row in first] == [date(2027, 1, 4)]
    finally:
        await executor.execute("DELETE FROM forecast_days WHERE tenant_id = %s", (tenant,))
        await executor.close()


async def test_a_batch_the_database_refuses_says_how_many_rows_it_did_not_take() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresForecastDayRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    day = date(2026, 10, 13)
    rows = [
        replace(
            _pod_day(tenant, "project-checkout", day, None),
            scope=CommitmentScope(
                kind=CommitmentScopeKind.POD, id=f"pod-{index:03d}", project_id="project-checkout"
            ),
        )
        for index in range(130)
    ]
    # A unit that cannot be stored ends the second statement (rows 100 to 129), not the first.
    rows[110] = replace(rows[110], unit=None)  # type: ignore[arg-type]
    try:
        with pytest.raises(ForecastDaysNotKept) as refused:
            await repository.save(rows)
        assert refused.value.rows == 30
        stored = await executor.fetch(
            "SELECT count(*) AS rows FROM forecast_days WHERE tenant_id = %s", (tenant,)
        )
        assert stored[0]["rows"] == 100
    finally:
        await executor.execute("DELETE FROM forecast_days WHERE tenant_id = %s", (tenant,))
        await executor.close()


async def _primary_key(executor: PsycopgAsyncExecutor) -> list[str]:
    rows = await executor.fetch(
        """
        SELECT attribute.attname AS name
        FROM pg_index AS key
        JOIN pg_attribute AS attribute
            ON attribute.attrelid = key.indrelid AND attribute.attnum = ANY(key.indkey)
        WHERE key.indrelid = 'forecast_days'::regclass AND key.indisprimary
        ORDER BY array_position(key.indkey, attribute.attnum)
        """
    )
    return [str(row["name"]) for row in rows]


async def test_the_project_key_migration_goes_down_and_up_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0044 down: a pod's day under two projects is dropped, a release's day under two
    keeps the row computed last, every other row stays; 0044 up again: the same key as before."""
    monkeypatch.setenv("OPENPROGRAM_DATABASE_URL", DATABASE_URL)
    get_settings.cache_clear()
    alembic = Config(str(ROOT / "backend/infra/persistence/alembic.ini"))
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    repository = PostgresForecastDayRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    day = date(2026, 10, 13)
    release = CommitmentScope(
        kind=CommitmentScopeKind.RELEASE, id="rel-1", project_id="project-checkout"
    )
    moved = CommitmentScope(
        kind=CommitmentScopeKind.RELEASE, id="rel-1", project_id="project-identity"
    )
    alone = CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-alone", project_id="project-a")
    rows = [
        _pod_day(tenant, "project-checkout", day, date(2026, 11, 3)),
        _pod_day(tenant, "project-identity", day, date(2026, 12, 1)),
        _pod_day(tenant, "project-checkout", day - timedelta(days=1), date(2026, 11, 4)),
        replace(_pod_day(tenant, "project-a", day, date(2026, 11, 5)), scope=alone),
        replace(_pod_day(tenant, "project-checkout", day, None), scope=release),
        replace(
            _pod_day(tenant, "project-identity", day, date(2026, 12, 2)),
            scope=moved,
            computed_at=AT + timedelta(hours=1),
        ),
    ]
    try:
        assert await _primary_key(executor) == [
            "tenant_id",
            "scope_kind",
            "scope_id",
            "project_id",
            "day",
        ]
        await repository.save(rows)

        command.downgrade(alembic, "0043_jira_hygiene")
        assert await _primary_key(executor) == ["tenant_id", "scope_kind", "scope_id", "day"]
        left = await executor.fetch(
            "SELECT scope_kind, scope_id, project_id, day FROM forecast_days "
            "WHERE tenant_id = %s ORDER BY scope_kind, scope_id, day",
            (tenant,),
        )
        assert [(r["scope_kind"], r["scope_id"], r["project_id"], r["day"]) for r in left] == [
            ("pod", "pod-alone", "project-a", day),
            # The shared pod's day under two projects is gone; its day under one stays.
            ("pod", "pod-platform", "project-checkout", day - timedelta(days=1)),
            ("release", "rel-1", "project-identity", day),
        ]

        command.upgrade(alembic, "head")
        assert await _primary_key(executor) == [
            "tenant_id",
            "scope_kind",
            "scope_id",
            "project_id",
            "day",
        ]
        await repository.save(rows[:2])
        for row in rows[:2]:
            assert await repository.list_between(tenant, row.scope, day, day) == [row]
    finally:
        command.upgrade(alembic, "head")
        await executor.execute("DELETE FROM forecast_days WHERE tenant_id = %s", (tenant,))
        await executor.close()
        get_settings.cache_clear()
