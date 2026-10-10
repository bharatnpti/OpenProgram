"""The tenant's Jira write switches: what is in force, where it comes from, and every change.

One place resolves them, for the admin panel and for every write path. The
master switch is the tenant write-back switch (``WriteBackConfigRepository``,
falling back to the deployment's ``jira_writeback_enabled``); the kinds and
the project allow-list are in ``JiraWritesRepository``. Each change of a
setting's value or source appends a row to the change log: who, when, the
value before and after.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from core.domain.jira_writes import (
    JiraWriteKind,
    JiraWrites,
    JiraWritesChange,
    JiraWritesUpdate,
    apply_update,
    changes_between,
    resolve_jira_writes,
)
from core.ports.repositories import JiraWritesRepository, WriteBackConfigRepository

#: The release readiness settings' own create switch, for a tenant saved before
#: the readiness kind existed here; None while readiness settings were never saved.
LegacyReadinessCreate = Callable[[str], Awaitable[bool | None]]


class JiraWritesService:
    def __init__(
        self,
        *,
        config_repository: WriteBackConfigRepository,
        repository: JiraWritesRepository,
        env_master: bool = False,
        env_master_set: bool = False,
        legacy_readiness_create: LegacyReadinessCreate | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config_repository
        self._repository = repository
        self._env_master = env_master
        self._env_master_set = env_master_set
        self._legacy_readiness_create = legacy_readiness_create
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def effective(self, tenant_id: str) -> JiraWrites:
        return resolve_jira_writes(
            master_override=await self._config.get_writeback_enabled(tenant_id),
            env_master=self._env_master,
            env_master_set=self._env_master_set,
            stored=await self._repository.get_jira_writes(tenant_id),
            legacy_readiness_create=await self._legacy(tenant_id),
        )

    async def kind_on(self, tenant_id: str, kind: JiraWriteKind) -> bool:
        """This kind's own switch, the master not included (the write path reads that itself)."""
        stored = await self._repository.get_jira_writes(tenant_id)
        legacy = await self._legacy(tenant_id) if kind is JiraWriteKind.READINESS_CREATE else None
        writes = resolve_jira_writes(
            master_override=None,
            env_master=False,
            env_master_set=False,
            stored=stored,
            legacy_readiness_create=legacy,
        )
        return writes.kind(kind).on

    async def update(self, tenant_id: str, update: JiraWritesUpdate, *, actor: str) -> JiraWrites:
        """Save what the update gives, audit each setting it changed, and return the result."""
        before = await self.effective(tenant_id)
        if update.empty:
            return before
        at = self._clock()
        if update.kinds or update.create_projects is not None:
            stored = await self._repository.get_jira_writes(tenant_id)
            await self._repository.save_jira_writes(
                tenant_id, apply_update(stored, update), at=at, actor=actor
            )
        if update.master is not None:
            await self._config.set_writeback_enabled(tenant_id, update.master)
        after = await self.effective(tenant_id)
        for change in changes_between(before, after, tenant_id=tenant_id, at=at, actor=actor):
            await self._repository.append_jira_writes_change(change)
        return after

    async def set_master(self, tenant_id: str, enabled: bool, *, actor: str) -> JiraWrites:
        return await self.update(tenant_id, JiraWritesUpdate(master=enabled), actor=actor)

    async def set_kind(
        self, tenant_id: str, kind: JiraWriteKind, on: bool, *, actor: str
    ) -> JiraWrites:
        return await self.update(tenant_id, JiraWritesUpdate(kinds={kind: on}), actor=actor)

    async def changes(self, tenant_id: str, *, limit: int = 20) -> list[JiraWritesChange]:
        return await self._repository.list_jira_writes_changes(tenant_id, limit)

    async def _legacy(self, tenant_id: str) -> bool | None:
        if self._legacy_readiness_create is None:
            return None
        return await self._legacy_readiness_create(tenant_id)


__all__ = ["JiraWritesService", "LegacyReadinessCreate"]
