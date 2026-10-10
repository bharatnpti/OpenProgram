"""Jira writes switches through the write paths: master AND kind, the defaults, the audit."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.jira_writes_service import JiraWritesService
from core.application.writeback_service import WriteBackOff, WriteBackService
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.jira_writes import (
    CreateProjects,
    JiraWriteKind,
    JiraWritesUpdate,
    SettingSource,
)
from core.domain.status import CheckInPreference, IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackStatus, WriteBackTarget
from infra.persistence.in_memory_jira_writes import InMemoryJiraWritesRepository
from tests.contract.fakes import (
    FakeIssueTracker,
    FakeStatusRepository,
    FakeWriteBackAuditRepository,
    FakeWriteBackConfigRepository,
)

TENANT = "demo"
DEV = "U1007"
ADMIN = "U1001"
AT = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
CHECKINS = JiraWriteKind.CHECKIN_UPDATES
CONSOLE = JiraWriteKind.CONSOLE_MOVES
CREATE = JiraWriteKind.READINESS_CREATE


class _World:
    def __init__(
        self, *, master: bool | None = True, legacy: bool | None = None, env: bool = False
    ) -> None:
        self.config = FakeWriteBackConfigRepository()
        if master is not None:
            self.config.enabled[TENANT] = master
        self.repository = InMemoryJiraWritesRepository()
        self.legacy = legacy

        async def legacy_create(tenant_id: str) -> bool | None:
            return self.legacy

        self.writes = JiraWritesService(
            config_repository=self.config,
            repository=self.repository,
            env_master=env,
            env_master_set=env,
            legacy_readiness_create=legacy_create,
            clock=lambda: AT,
        )
        self.tracker = FakeIssueTracker(
            issues={
                key: Issue(
                    tenant_id=TENANT,
                    key=key,
                    title=f"Work on {key}",
                    state=IssueState.IN_PROGRESS,
                    assignee=UserRef(tenant_id=TENANT, external_id=DEV),
                )
                for key in ("CHK-1", "CHK-2")
            }
        )
        self.audit = FakeWriteBackAuditRepository()
        self.status = FakeStatusRepository()
        self.consent(WriteBackConsent.AUTO_APPLY)
        self.service = WriteBackService(
            issue_tracker=self.tracker,
            audit_repository=self.audit,
            config_repository=self.config,
            status_repository=self.status,
            kind_switch=self.writes.kind_on,
            clock=lambda: AT,
        )

    def consent(self, consent: WriteBackConsent) -> None:
        self.status.checkin_preferences[(TENANT, DEV)] = CheckInPreference(
            tenant_id=TENANT, developer_id=DEV, write_back_consent=consent
        )

    async def set(self, **kinds: bool) -> None:
        await self.writes.update(
            TENANT,
            JiraWritesUpdate(kinds={JiraWriteKind(kind): on for kind, on in kinds.items()}),
            actor=ADMIN,
        )

    async def check_in(self, key: str = "CHK-1") -> list[WriteBackStatus]:
        rows = await self.service.apply_from_checkin(
            tenant_id=TENANT,
            developer_id=DEV,
            correlation_id=f"checkin-{key}",
            claims=[IssueClaim(issue_key=key, claimed_done=True)],
        )
        return [row.status for row in rows]

    async def console(self, key: str = "CHK-2") -> str:
        moved = await self.service.apply_from_console(
            tenant_id=TENANT,
            developer_id=DEV,
            correlation_id=f"console-{key}",
            issue_key=key,
            target=WriteBackTarget.DONE,
        )
        return moved.outcome


async def test_master_on_and_nothing_set_writes_check_ins_as_before() -> None:
    world = _World(master=True)

    assert await world.check_in() == [WriteBackStatus.APPLIED]
    assert world.tracker.transitions == [(TENANT, "CHK-1", "done")]
    assert await world.service.standing_consent_open(TENANT, DEV) is True


async def test_master_off_writes_nothing_whatever_the_kinds_say() -> None:
    world = _World(master=False)
    await world.set(checkin_updates=True, console_moves=True)

    assert await world.check_in() == []
    assert await world.console() == "off"
    assert await world.service.write_back_mode(TENANT, DEV) == "off"
    assert world.tracker.transitions == []


async def test_check_ins_off_stops_only_the_check_in_path() -> None:
    world = _World(master=True)
    await world.set(checkin_updates=False)

    assert await world.check_in() == []
    assert await world.service.standing_consent_open(TENANT, DEV) is False
    assert await world.service.asks_before_writing(TENANT, DEV) is False
    dry = await world.service.dry_run(
        tenant_id=TENANT,
        developer_id=DEV,
        claims=[IssueClaim(issue_key="CHK-1", claimed_done=True)],
    )
    assert dry.written == frozenset() and dry.proposed == frozenset()
    # A person's own console move is its own kind, still on.
    assert await world.service.write_back_mode(TENANT, DEV) == "auto"
    assert await world.console() == "applied"
    assert world.tracker.transitions == [(TENANT, "CHK-2", "done")]


async def test_console_moves_off_stops_only_the_console_path() -> None:
    world = _World(master=True)
    await world.set(console_moves=False)

    assert await world.service.write_back_mode(TENANT, DEV) == "off"
    assert await world.console() == "off"
    assert await world.check_in() == [WriteBackStatus.APPLIED]
    assert world.tracker.transitions == [(TENANT, "CHK-1", "done")]


async def test_a_pending_proposal_is_not_written_once_check_ins_are_turned_off() -> None:
    world = _World(master=True)
    world.consent(WriteBackConsent.ALWAYS_ASK)
    assert await world.check_in() == [WriteBackStatus.PROPOSED]
    await world.set(checkin_updates=False)

    answered = await world.service.resolve_consent_reply(
        tenant_id=TENANT, developer_id=DEV, correlation_id="checkin-CHK-1", reply_text="yes"
    )

    assert answered == []
    assert world.tracker.transitions == []


async def test_a_revert_needs_the_master_switch_but_not_the_kind() -> None:
    world = _World(master=True)
    [applied] = await world.service.apply_from_checkin(
        tenant_id=TENANT,
        developer_id=DEV,
        correlation_id="checkin-1",
        claims=[IssueClaim(issue_key="CHK-1", claimed_done=True)],
    )
    await world.set(checkin_updates=False)
    world.config.enabled[TENANT] = False

    with pytest.raises(WriteBackOff, match="Jira writes are off for this tenant."):
        await world.service.revert(applied)
    world.config.enabled[TENANT] = True
    reverted = await world.service.revert(applied)

    assert reverted is not None and reverted.status is WriteBackStatus.REVERTED


async def test_effective_values_say_where_each_comes_from() -> None:
    nothing = await _World(master=None).writes.effective(TENANT)
    env = await _World(master=None, env=True).writes.effective(TENANT)
    legacy = await _World(master=True, legacy=True).writes.effective(TENANT)

    assert (nothing.master.on, nothing.master.source) == (False, SettingSource.DEFAULT)
    assert (env.master.on, env.master.source) == (True, SettingSource.ENV)
    assert (legacy.master.source, legacy.kind(CREATE).on) == (SettingSource.ADMIN, True)
    assert legacy.kind(CREATE).source is SettingSource.ADMIN
    assert nothing.kind(CHECKINS).source is SettingSource.DEFAULT


async def test_every_change_is_audited_with_who_when_and_before_after() -> None:
    world = _World(master=None)

    await world.writes.update(
        TENANT,
        JiraWritesUpdate(
            master=True,
            kinds={CREATE: True},
            create_projects=CreateProjects(own_project=True, projects=("SEC",)),
        ),
        actor=ADMIN,
    )
    await world.writes.update(TENANT, JiraWritesUpdate(kinds={CREATE: True}), actor=ADMIN)
    await world.writes.set_kind(TENANT, CREATE, False, actor="U1002")
    changes = await world.writes.changes(TENANT)

    # Newest first; the second update gave nothing new, so it left no row.
    assert [(c.actor, c.setting, c.before, c.after) for c in changes] == [
        ("U1002", "readiness_create", True, False),
        (
            "U1001",
            "create_projects",
            CreateProjects(),
            CreateProjects(own_project=True, projects=("SEC",)),
        ),
        ("U1001", "readiness_create", False, True),
        ("U1001", "master", False, True),
    ]
    assert {c.at for c in changes} == {AT}
    assert changes[-1].before_source is SettingSource.DEFAULT
    assert changes[0].before_source is SettingSource.ADMIN


async def test_an_empty_update_changes_and_audits_nothing() -> None:
    world = _World(master=True)

    await world.writes.update(TENANT, JiraWritesUpdate(), actor=ADMIN)

    assert await world.writes.changes(TENANT) == []
    assert await world.repository.get_jira_writes(TENANT) is None
