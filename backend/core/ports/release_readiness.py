from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from core.domain.release_readiness import (
    Finding,
    ReadinessAction,
    ReadinessRun,
    ReadinessSettings,
    ReleaseCriterion,
    ScopeRef,
    Suggestion,
)


class ReleaseReadinessRepository(Protocol):
    """Release readiness state: settings, criteria, findings, drafts, runs and the audit.

    The audit (``append_action``) is append-only. A finding is unique per
    criterion and scope, a draft per finding, and a run per slot.
    """

    async def get_settings(self, tenant_id: str) -> ReadinessSettings | None:
        """The tenant's settings; None while it has saved none."""
        ...

    async def save_settings(self, settings: ReadinessSettings) -> None: ...

    async def list_criteria(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[ReleaseCriterion]: ...

    async def save_criterion(self, criterion: ReleaseCriterion) -> None: ...

    async def list_findings(self, tenant_id: str, scopes: Sequence[ScopeRef]) -> list[Finding]:
        """Every finding of these scopes, of any criterion."""
        ...

    async def get_finding(self, tenant_id: str, finding_id: str) -> Finding | None: ...

    async def save_finding(self, finding: Finding) -> None:
        """Insert or replace by id; one finding per criterion and scope."""
        ...

    async def list_suggestions(
        self, tenant_id: str, finding_ids: Sequence[str]
    ) -> list[Suggestion]: ...

    async def get_suggestion(self, tenant_id: str, suggestion_id: str) -> Suggestion | None: ...

    async def save_suggestion(self, suggestion: Suggestion) -> None:
        """Insert or replace by id; a second draft for the same finding is refused."""
        ...

    async def claim_suggestion(
        self, tenant_id: str, suggestion_id: str, *, version: int, at: datetime
    ) -> bool:
        """Move one open draft at this version to creating, atomically; False if not claimed."""
        ...

    async def append_action(self, action: ReadinessAction) -> None: ...

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[ReadinessAction]:
        """One finding's trail, oldest first, its draft's rows included."""
        ...

    async def claim_run(self, run: ReadinessRun) -> bool:
        """Record a run for its slot; False when the slot already has one."""
        ...

    async def finish_run(self, run: ReadinessRun) -> None: ...

    async def last_run(self, tenant_id: str) -> ReadinessRun | None:
        """The newest finished run, scheduled or manual."""
        ...
