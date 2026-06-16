from __future__ import annotations

from typing import Protocol


class ReadinessProbe(Protocol):
    async def check(self) -> bool: ...
