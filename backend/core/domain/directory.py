from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.domain.graph import JsonScalar


@dataclass(frozen=True, kw_only=True)
class DirectoryUser:
    tenant_id: str
    external_id: str
    display_name: str
    email: str | None = None
    handle: str | None = None
    avatar_url: str | None = None
    title: str | None = None
    is_active: bool = True
    source: str = "directory"
    synced_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    metadata: dict[str, JsonScalar] = field(default_factory=dict)
