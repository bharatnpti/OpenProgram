from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol

from core.domain.hygiene import (
    HygieneAction,
    HygieneFinding,
    HygieneRule,
    HygieneRun,
    HygieneSettings,
    HygieneState,
)
from core.domain.release_readiness import ScopeRef


class HygieneRepository(Protocol):
    """Jira hygiene state: settings, rules, findings, runs and the audit.

    The audit (``append_action``) is append-only. At most one open finding exists
    per rule, issue and scope; a run claims its slot once.
    """

    async def get_settings(self, tenant_id: str) -> HygieneSettings | None:
        """The tenant's settings; None while it has saved none."""
        ...

    async def save_settings(self, settings: HygieneSettings) -> None: ...

    async def list_rules(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[HygieneRule]: ...

    async def save_rule(self, rule: HygieneRule) -> None: ...

    async def list_findings(
        self,
        tenant_id: str,
        scopes: Sequence[ScopeRef] | None = None,
        *,
        states: Collection[HygieneState] | None = None,
    ) -> list[HygieneFinding]:
        """The findings of these scopes (every scope for None), in these states (all for None)."""
        ...

    async def get_finding(self, tenant_id: str, finding_id: str) -> HygieneFinding | None: ...

    async def save_finding(self, finding: HygieneFinding) -> None:
        """Insert or replace by id; a second open finding per rule, issue and scope is refused."""
        ...

    async def append_action(self, action: HygieneAction) -> None: ...

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[HygieneAction]:
        """One finding's trail, oldest first."""
        ...

    async def claim_run(self, run: HygieneRun) -> bool:
        """Record a run for its slot; False when the slot already has one."""
        ...

    async def finish_run(self, run: HygieneRun) -> None: ...

    async def last_run(self, tenant_id: str) -> HygieneRun | None:
        """The newest finished run, scheduled or manual."""
        ...
