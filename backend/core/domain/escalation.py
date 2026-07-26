from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EscalationTarget(StrEnum):
    """Who receives a check-in nudge at a given rung of the escalation ladder."""

    DEVELOPER = "developer"
    SCRUM_MASTER = "scrum_master"
    MANAGER = "manager"


@dataclass(frozen=True, kw_only=True)
class EscalationContact:
    target: EscalationTarget
    chat_external_id: str
    display_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class PodEscalationContacts:
    """Human escalation contacts configured on a pod (default-empty)."""

    scrum_master: EscalationContact | None = None
    manager: EscalationContact | None = None

    def contact_for(self, target: EscalationTarget) -> EscalationContact | None:
        if target is EscalationTarget.SCRUM_MASTER:
            return self.scrum_master
        if target is EscalationTarget.MANAGER:
            return self.manager
        return None


@dataclass(frozen=True, kw_only=True)
class EscalationStep:
    """One rung of the ladder: wait, then nudge the given target."""

    target: EscalationTarget
    wait_seconds: int


@dataclass(frozen=True, kw_only=True)
class EscalationPolicy:
    steps: tuple[EscalationStep, ...]

    def step(self, number: int) -> EscalationStep | None:
        """Return the 1-indexed step, or None when the ladder is exhausted."""
        if 1 <= number <= len(self.steps):
            return self.steps[number - 1]
        return None


def default_escalation_policy(
    *,
    developer_wait_seconds: int,
    scrum_master_wait_seconds: int,
    manager_wait_seconds: int,
    escalate_to_scrum_master: bool = True,
    escalate_to_manager: bool = True,
) -> EscalationPolicy:
    """Build the dev -> SM -> manager ladder.

    The first rung always re-pings the developer (preserving the historical
    single-nudge behaviour); later rungs are added only when enabled and are
    delivered to pod escalation contacts. A non-positive wait drops that rung.
    """
    steps: list[EscalationStep] = [
        EscalationStep(target=EscalationTarget.DEVELOPER, wait_seconds=developer_wait_seconds),
    ]
    if escalate_to_scrum_master and scrum_master_wait_seconds > 0:
        steps.append(
            EscalationStep(
                target=EscalationTarget.SCRUM_MASTER,
                wait_seconds=scrum_master_wait_seconds,
            )
        )
    if escalate_to_manager and manager_wait_seconds > 0:
        steps.append(
            EscalationStep(target=EscalationTarget.MANAGER, wait_seconds=manager_wait_seconds)
        )
    return EscalationPolicy(steps=tuple(steps))
