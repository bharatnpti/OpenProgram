"""What a day report says of Jira hygiene: one count line, and the asking rules' asks.

The count line ("Jira hygiene: 7 open (2 new today).") sits under Where we stand,
for every rule that is on. Only a rule an admin switched to ask adds asks: one
per owner per rule per scope, naming up to five keys, waiting from the earliest
finding's day (the change-label rule's from the day the addition passed
planning's grace). Each climbs the project's escalation matrix like any other
ask (``day_report_asks.escalated``), from the pod the owner was found in. An
owner the scan could not name is asked of whoever decides on the project
(``readiness_decider``). Nothing here counts or names anyone else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from core.application.day_report_asks import Ask, AskScope, readiness_decider, trimmed
from core.application.hygiene_service import HygieneReport
from core.domain.escalation_matrix import EscalationMatrix, NeedType
from core.domain.release_readiness import ScopeKind
from core.domain.report_facts import ProgressFacts
from core.domain.reports import ReportGroup

HEADING = "Jira hygiene"


@dataclass(frozen=True, kw_only=True)
class HygienePart:
    asks: tuple[Ask, ...] = ()
    #: None while the scan is off or no rule is on: the report says nothing of it.
    line: str | None = None

    def groups(self) -> tuple[ReportGroup, ...]:
        """Where we stand's group for the count line."""
        return (ReportGroup(heading=HEADING, lines=(self.line,)),) if self.line else ()

    def progress(self, facts: ProgressFacts) -> ProgressFacts:
        """The console's Where we stand, with the count line under its notes."""
        return replace(facts, notes=(*facts.notes, self.line)) if self.line else facts


def hygiene_part(
    report: HygieneReport, scope: AskScope, matrix: EscalationMatrix, day: date
) -> HygienePart:
    asks: list[Ask] = []
    for item in report.asks:
        owner = scope.names.get(item.owner_member_id or "")
        team = scope.all_teams.get(item.pod_id or "") or scope.team_of_person(item.owner_member_id)
        if owner is None:
            pod = item.scope.id if item.scope.kind is ScopeKind.POD else None
            decider = readiness_decider(scope, matrix, pod)
            owner, team = decider.owner, team or decider.team
        asks.append(
            Ask(
                need=NeedType(item.need.value),
                owner=owner,
                text=trimmed(item.text),
                waited_days=max(0, (day - item.waits_from).days),
                team=team,
                issue_key=item.keys[0] if len(item.keys) == 1 else None,
            )
        )
    return HygienePart(asks=tuple(asks), line=report.line)
