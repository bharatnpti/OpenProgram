from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from core.domain.gates import GateItem, GateTemplate, TrackedQuestion


@dataclass
class InMemoryGateTemplateRepository:
    _templates: dict[tuple[str, str], GateTemplate] = field(default_factory=dict)

    async def list(self, tenant_id: str) -> list[GateTemplate]:
        return [t for (tenant, _), t in self._templates.items() if tenant == tenant_id]

    async def save(self, template: GateTemplate) -> None:
        self._templates[(template.tenant_id, template.template_id)] = template

    async def delete(self, tenant_id: str, template_id: str) -> bool:
        return self._templates.pop((tenant_id, template_id), None) is not None


@dataclass
class InMemoryGateItemRepository:
    _items: dict[tuple[str, str], GateItem] = field(default_factory=dict)

    async def list_for_issues(self, tenant_id: str, issue_keys: Sequence[str]) -> list[GateItem]:
        keys = set(issue_keys)
        return sorted(
            (i for (t, _), i in self._items.items() if t == tenant_id and i.issue_key in keys),
            key=lambda item: (item.issue_key, item.created_at),
        )

    async def get(self, tenant_id: str, item_id: str) -> GateItem | None:
        return self._items.get((tenant_id, item_id))

    async def save(self, item: GateItem) -> None:
        for key, existing in list(self._items.items()):
            if (
                existing.tenant_id == item.tenant_id
                and existing.fingerprint == item.fingerprint
                and existing.item_id != item.item_id
            ):
                del self._items[key]
        self._items[(item.tenant_id, item.item_id)] = item


@dataclass
class InMemoryQuestionRepository:
    _questions: dict[tuple[str, str], TrackedQuestion] = field(default_factory=dict)

    async def list_for_issues(
        self, tenant_id: str, issue_keys: Sequence[str]
    ) -> list[TrackedQuestion]:
        keys = set(issue_keys)
        return sorted(
            (q for (t, _), q in self._questions.items() if t == tenant_id and q.issue_key in keys),
            key=lambda question: question.asked_at,
        )

    async def get(self, tenant_id: str, question_id: str) -> TrackedQuestion | None:
        return self._questions.get((tenant_id, question_id))

    async def save(self, question: TrackedQuestion) -> None:
        self._questions[(question.tenant_id, question.question_id)] = question


@dataclass
class InMemoryIssueScanRepository:
    _scans: dict[tuple[str, str], datetime | None] = field(default_factory=dict)

    async def last_scanned(self, tenant_id: str, issue_key: str) -> datetime | None:
        return self._scans.get((tenant_id, issue_key))

    async def record(
        self,
        tenant_id: str,
        issue_key: str,
        issue_updated_at: datetime | None,
        scanned_at: datetime,
    ) -> None:
        self._scans[(tenant_id, issue_key)] = issue_updated_at or scanned_at
