from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from core.domain.gates import GateItem, GateTemplate, TrackedQuestion


class GateTemplateRepository(Protocol):
    async def list(self, tenant_id: str) -> list[GateTemplate]:
        """The tenant's templates; empty until an admin saves one (defaults apply)."""
        ...

    async def save(self, template: GateTemplate) -> None: ...

    async def delete(self, tenant_id: str, template_id: str) -> bool: ...


class GateItemRepository(Protocol):
    async def list_for_issues(self, tenant_id: str, issue_keys: Sequence[str]) -> list[GateItem]:
        """Every item on these issues, dismissed suggestions included."""
        ...

    async def get(self, tenant_id: str, item_id: str) -> GateItem | None: ...

    async def save(self, item: GateItem) -> None: ...


class QuestionRepository(Protocol):
    async def list_for_issues(
        self, tenant_id: str, issue_keys: Sequence[str]
    ) -> list[TrackedQuestion]: ...

    async def get(self, tenant_id: str, question_id: str) -> TrackedQuestion | None: ...

    async def save(self, question: TrackedQuestion) -> None: ...


class IssueScanRepository(Protocol):
    """When each issue's text was last read, so an unchanged issue is not read again."""

    async def last_scanned(self, tenant_id: str, issue_key: str) -> datetime | None:
        """The issue's own updated time when it was last read; None if never."""
        ...

    async def record(
        self,
        tenant_id: str,
        issue_key: str,
        issue_updated_at: datetime | None,
        scanned_at: datetime,
    ) -> None: ...
