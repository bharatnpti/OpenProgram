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
