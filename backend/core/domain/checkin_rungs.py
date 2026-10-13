"""The rungs of a day's check-in silence ladder that went out, and whom each was for.

When someone does not answer the day's check-in, the nudge workflow climbs a
ladder (``infra/workflows/nudge.py``): a reminder to the person, then a notice
to their scrum master, then to their manager, each after its wait. A rung that
goes out is kept in ``checkin_nudges`` by its number only. The workflow also
records each rung's outcome as a fact (``rung_fact``): whom it was for and what
happened (sent, or why not). A rung sent before those facts existed is read
against today's ladder, and says so (``recorded`` is False).

Only rungs that went out are shown, with when; never a message's text, no count
across days, and nothing ranks anyone. Kept free of application imports: the
workflow runtime loads this module inside its sandbox.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from core.domain.escalation import EscalationPolicy, EscalationTarget
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.status import CheckInNudgeSent

#: The fact a rung's outcome is kept as, one per check-in and rung number.
CHECKIN_RUNG_FACT_SOURCE = "checkin_rung"
#: The outcome of a rung that went out; the others are the workflow's suppressions.
RUNG_SENT = "sent"


@dataclass(frozen=True, kw_only=True)
class RungView:
    """One rung that went out for a person's check-in of the day."""

    number: int
    #: Whom it was for; None when it was not recorded and today's ladder has no such rung.
    target: EscalationTarget | None
    sent_at: datetime
    #: Its target was recorded when it went out; otherwise it is today's ladder's.
    recorded: bool


def rung_fact(
    *,
    tenant_id: str,
    pod_id: str,
    developer_id: str,
    correlation_id: str,
    checkin_date: str,
    nudge_number: int,
    target: EscalationTarget,
    action: str,
    observed_at: datetime,
) -> FactEvent:
    """The fact of one rung's outcome: whom it was for and what happened, no message text.

    Kept on the pod, not the person: a person's own facts are what their
    check-in reads back to them, and a rung is not their work.
    """
    payload: dict[str, JsonScalar] = {
        "developer_id": developer_id,
        "correlation_id": correlation_id,
        "checkin_date": checkin_date,
        "nudge_number": nudge_number,
        "target": target.value,
        "action": action,
    }
    return FactEvent(
        tenant_id=tenant_id,
        source=CHECKIN_RUNG_FACT_SOURCE,
        entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.POD, id=pod_id),
        payload=payload,
        observed_at=observed_at,
        correlation_id=f"checkin-rung:{correlation_id}:{nudge_number}",
    )


def rung_facts_since(day: date) -> datetime:
    """Where to start reading a day's rung facts: the UTC midnight before it, for every zone."""
    return datetime.combine(day - timedelta(days=1), time.min, UTC)


def sent_rungs(
    nudges: Sequence[CheckInNudgeSent],
    facts: Iterable[FactEvent],
    policy: EscalationPolicy | None,
    day: date,
) -> Mapping[str, tuple[RungView, ...]]:
    """Each person's rungs that went out for ``day``'s check-in, by person, in order.

    A rung's target is the one its fact recorded; without one, the rung of
    that number on today's ladder (``recorded`` False). A day can hold more
    than one check-in for a person (a second round); each rung is listed once.
    """
    recorded: dict[tuple[str, int], EscalationTarget] = {}
    for fact in facts:
        payload = fact.payload
        if fact.source != CHECKIN_RUNG_FACT_SOURCE:
            continue
        if payload.get("checkin_date") != day.isoformat():
            continue
        correlation = payload.get("correlation_id")
        number = payload.get("nudge_number")
        if not isinstance(correlation, str) or not isinstance(number, int):
            continue
        try:
            recorded[(correlation, number)] = EscalationTarget(str(payload.get("target")))
        except ValueError:
            continue
    by_person: dict[str, list[RungView]] = {}
    for nudge in nudges:
        target = recorded.get((nudge.correlation_id, nudge.nudge_number))
        by_person.setdefault(nudge.developer_id, []).append(
            RungView(
                number=nudge.nudge_number,
                target=target if target is not None else ladder_target(policy, nudge.nudge_number),
                sent_at=nudge.sent_at,
                recorded=target is not None,
            )
        )
    return {
        person: tuple(sorted(rungs, key=lambda rung: (rung.sent_at, rung.number)))
        for person, rungs in by_person.items()
    }


def ladder_target(policy: EscalationPolicy | None, number: int) -> EscalationTarget | None:
    """Whom rung ``number`` of today's ladder goes to; an empty ladder still reminds once."""
    if policy is None:
        return EscalationTarget.DEVELOPER if number == 1 else None
    step = policy.step(number)
    if step is not None:
        return step.target
    if not policy.steps and number == 1:
        # The workflow's own fallback (NudgeInput.resolved_steps): one reminder.
        return EscalationTarget.DEVELOPER
    return None
