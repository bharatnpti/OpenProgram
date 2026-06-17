from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from core.domain.graph import JsonScalar


class AgentTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> Mapping[str, object]: ...

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str: ...
