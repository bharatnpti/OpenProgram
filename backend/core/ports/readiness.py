from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ReadinessProbe(Protocol):
    async def check(self) -> bool: ...


@dataclass(frozen=True)
class ReadinessReport:
    """Whether one dependency is ready, plus a short reason an operator can act on.

    ``detail`` is plain operator-facing text such as
    ``"unauthorized: the endpoint rejected the API key (HTTP 401)"``. It is
    surfaced on the unauthenticated ``/ready`` endpoint, so it must never carry
    credentials, response bodies or connection strings.
    """

    ready: bool
    detail: str | None = None


@runtime_checkable
class ReportingReadinessProbe(ReadinessProbe, Protocol):
    """A probe that can say why it is (not) ready, not just whether."""

    async def report(self) -> ReadinessReport: ...
