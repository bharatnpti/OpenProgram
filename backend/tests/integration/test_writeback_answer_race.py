"""The chat and the console answering one proposal at once write the issue once, in Postgres.

Two executors stand for two processes (the worker answering the DM, the API
answering the console): two pools, so the per-correlation advisory lock is the
only thing between them. Never point it at a database anyone uses: run it on a
copy, under a tenant of its own that the test removes again:

    export OPENPROGRAM_WRITEBACK_RACE_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/<copy>
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_writeback_answer_race.py --no-cov
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core.application.writeback_service import (
    CONSENT_REPLY_SOURCE,
    CONSOLE_CONSENT_SOURCE,
    WriteBackService,
)
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_graph import PostgresGraphRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor
from tests.contract.fakes import FakeIssueTracker

DATABASE_URL = os.getenv("OPENPROGRAM_WRITEBACK_RACE_DATABASE_URL", "")
ROUNDS = 8
_DEV = "U-race"
_AT = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_WRITEBACK_RACE_DATABASE_URL",
    ),
]


class _SlowTracker(FakeIssueTracker):
    """A transition that takes a while: the window two answers race in."""

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        await asyncio.sleep(0.05)
        await super().transition(tenant_id, key, to_state)


class _Unlocked(PostgresGraphRepository):
    @asynccontextmanager
    async def exclusive_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> AsyncIterator[None]:
        yield


async def _race(repository_type: type[PostgresGraphRepository]) -> tuple[int, int]:
    """Two answers per round from two pools; the transitions and applied rows each round made."""
    tenant = f"race-{uuid4().hex[:8]}"
    executors = [PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=3) for _ in range(2)]
    repositories = [repository_type(executor) for executor in executors]
    gates = InMemoryGraphStore()
    await gates.set_writeback_enabled(tenant, True)
    tracker = _SlowTracker(
        issues={
            "RACE-1": Issue(
                tenant_id=tenant,
                key="RACE-1",
                title="Race",
                state=IssueState.TODO,
                assignee=UserRef(tenant_id=tenant, external_id=_DEV),
            )
        }
    )
    services = [
        WriteBackService(
            issue_tracker=tracker,
            audit_repository=repository,
            config_repository=gates,
            status_repository=gates,
        )
        for repository in repositories
    ]
    transitions = applied = 0
    try:
        for round_number in range(ROUNDS):
            correlation = f"corr-{round_number}"
            proposal = WriteBackAudit(
                id=uuid4().hex,
                tenant_id=tenant,
                developer_id=_DEV,
                issue_key="RACE-1",
                correlation_id=correlation,
                status=WriteBackStatus.PROPOSED,
                target_state="in_progress",
                before_state="todo",
                after_state="in_progress",
                source="checkin",
                created_at=_AT,
            )
            await repositories[0].record(proposal)
            before = len(tracker.transitions)
            await asyncio.gather(
                services[0].resolve_proposal(
                    tenant_id=tenant,
                    developer_id=_DEV,
                    audit_id=proposal.id,
                    decision="yes",
                    source=CONSOLE_CONSENT_SOURCE,
                ),
                services[1].resolve_proposal(
                    tenant_id=tenant,
                    developer_id=_DEV,
                    audit_id=proposal.id,
                    decision="yes",
                    source=CONSENT_REPLY_SOURCE,
                ),
            )
            transitions = max(transitions, len(tracker.transitions) - before)
            rows = await repositories[1].list_writeback_by_correlation(tenant, correlation)
            applied = max(applied, sum(row.status is WriteBackStatus.APPLIED for row in rows))
    finally:
        await executors[0].execute("DELETE FROM writeback_audit WHERE tenant_id = %s", (tenant,))
        for executor in executors:
            await executor.close()
    return transitions, applied


async def test_two_processes_answering_one_proposal_write_it_once() -> None:
    assert await _race(PostgresGraphRepository) == (1, 1)


async def test_without_the_advisory_lock_the_same_race_writes_twice() -> None:
    """The control: the same two answers with the lock left out move the issue twice."""
    transitions, applied = await _race(_Unlocked)

    assert transitions == 2
    assert applied == 2
