"""A day report with something in every section, on the seeded test tenant.

Kept apart from the tests and free of anything newer than the report's sections,
so the same day can be built by an older builder: the golden files in
``tests/unit/golden`` were written that way, before the console's facts existed.

    PYTHONPATH=backend uv run python -m tests.fixtures.day_report_full_day <dir>

writes the four formats a send uses into ``<dir>``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from core.application.release_readiness_service import ReleaseReadinessService
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.escalation_matrix import default_matrix
from core.domain.forecast import CommitmentScope, CommitmentScopeKind, DateChange, Verdict
from core.domain.gates import GateItem, ItemSource, ItemStatus, QuestionStatus, TrackedQuestion
from core.domain.release_readiness import (
    AppliesTo,
    Matcher,
    MatcherKind,
    ReadinessSettings,
    ReleaseCriterion,
    Severity,
    Strength,
)
from core.domain.reports import DayReport, DayReportNote, render_text
from infra.adapters.reports.render import email_html, slack_text, teams_payload
from infra.registry import ServiceRegistry
from tests.unit.test_day_reports import NOW, TENANT, TODAY, _seeded_registry

S = DeliveryStage

NOTE = DayReportNote(
    tenant_id=TENANT,
    report_id="rep-1",
    report_date=TODAY,
    text="The vendor's sandbox is back. Refunds are next.",
    author="dev-priya",
    updated_at=NOW,
)


async def a_full_day() -> ServiceRegistry:
    """The seeded tenant on a day with something in every section.

    A previous snapshot (two moves and a new requirement), a delivery date moved
    once, a decision owner, a pending gate item and a partly answered question,
    on top of the seed's blockers and request.
    """
    registry, _store = await _seeded_registry()
    await registry.requirements_snapshot_repository().save(
        RequirementsSnapshot(
            tenant_id=TENANT,
            project_id="checkout",
            day=date(2026, 10, 2),
            stage_counts={**{stage: 0 for stage in S}, S.IN_DEVELOPMENT: 2},
            stage_points={stage: 0.0 for stage in S},
            has_points=False,
            excluded=0,
            unmapped_statuses=(),
            items={"CHK-1": S.IN_DEVELOPMENT, "CHK-2": S.IN_DEVELOPMENT},
            titles={"CHK-1": "CHK-1 work", "CHK-2": "CHK-2 work"},
            computed_at=NOW,
        )
    )
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")
    for when, target, note in (
        (datetime(2026, 9, 20, 9, 0, tzinfo=UTC), date(2026, 11, 14), ""),
        (datetime(2026, 10, 4, 9, 0, tzinfo=UTC), date(2026, 11, 21), "Vendor moved its test date"),
    ):
        await registry.commitment_repository().append(
            DateChange(
                tenant_id=TENANT,
                scope=scope,
                target_date=target,
                changed_at=when,
                changed_by="dev-priya",
                note=note,
            )
        )
    await registry.escalation_matrix_service().save(
        replace(default_matrix(TENANT, "checkout"), decision_owner_id="dev-priya"), actor="admin"
    )
    _templates, items, questions, _scans = registry.gate_repositories()
    await items.save(
        GateItem(
            tenant_id=TENANT,
            item_id="g-1",
            issue_key="CHK-1",
            template_id="business-acceptance",
            kind="acceptance",
            text="A saved card pays without the CVC",
            status=ItemStatus.PENDING,
            source=ItemSource.DESCRIPTION,
            source_ref="description",
            created_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
            created_by="scan",
        )
    )
    await questions.save(
        TrackedQuestion(
            tenant_id=TENANT,
            question_id="q-1",
            issue_key="CHK-1",
            comment_ref="c-1",
            asked_by="acc-asha",
            asked_by_name="Asha",
            asked_to="dev-priya",
            asked_to_name="Priya",
            asked_at=datetime(2026, 9, 29, 9, 0, tzinfo=UTC),
            summary="Is 3-D Secure in scope?",
            status=QuestionStatus.PARTLY,
            confirmed=True,
        )
    )
    return registry


class WithVerdict:
    """A forecast service whose verdict is replaced, to put the date in danger or out of it."""

    def __init__(self, inner: object, verdict: Verdict) -> None:
        self._inner = inner
        self._verdict = verdict

    async def scope_delivery(self, *args: object) -> object:
        view = await self._inner.scope_delivery(*args)  # type: ignore[attr-defined]
        return replace(view, verdict=self._verdict)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


async def full_report(verdict: Verdict | None = None, *, note: bool = True) -> DayReport:
    """The day's report; with ``verdict``, the forecast's verdict replaced by it."""
    builder = (await a_full_day()).day_report_builder()
    if verdict is not None:
        builder._forecast = WithVerdict(builder._forecast, verdict)  # type: ignore[assignment]
    return await builder.build(
        TENANT, "checkout", TODAY, report_id="rep-1", note=NOTE if note else None
    )


def formats(report: DayReport) -> dict[str, str]:
    """What a send carries: the plain text (Send now and the schedule), a direct
    message, an email and a Teams card."""
    return {
        "text.txt": render_text(report),
        "slack.txt": slack_text(report),
        "email.html": email_html(report),
        "teams.json": json.dumps(teams_payload(report), indent=2, sort_keys=True) + "\n",
    }


#: Each golden day: the file stem and the verdict the forecast is made to give.
DAYS: dict[str, Verdict | None] = {
    "day_report_full_day": None,
    "day_report_full_day_at_risk": Verdict.AT_RISK,
}

#: The full day with release readiness on and two blocking gaps close to their date.
READINESS_DAY = "day_report_readiness_gap"


async def readiness_report() -> DayReport:
    """The full day, with release criteria checked: a blocking security review the
    project lacks while CHK-2 is already in production, the Payments Pod's on-call
    handover due within four working days, and an advisory check the report leaves out."""
    registry = await a_full_day()
    await registry.commitment_repository().append(
        DateChange(
            tenant_id=TENANT,
            scope=CommitmentScope(
                kind=CommitmentScopeKind.POD, id="pod-pay", project_id="checkout"
            ),
            target_date=date(2026, 10, 23),
            changed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            changed_by="dev-priya",
        )
    )
    readiness = replace_service_clock(registry)
    await registry.release_readiness_repository().save_settings(
        ReadinessSettings(tenant_id=TENANT, enabled=True)
    )
    for name, applies_to, value, severity in (
        ("Security review", AppliesTo.RELEASE, "security-review", Severity.BLOCKING),
        ("On-call handover", AppliesTo.POD, "on-call", Severity.BLOCKING),
        ("Accessibility check", AppliesTo.RELEASE, "accessibility", Severity.ADVISORY),
    ):
        await readiness.save_criterion(
            ReleaseCriterion(
                tenant_id=TENANT,
                criterion_id="",
                name=name,
                evidence=f"A {name.lower()} before production.",
                applies_to=applies_to,
                severity=severity,
                matchers=(
                    Matcher(kind=MatcherKind.LABEL, value=value, strength=Strength.EVIDENCE),
                ),
            ),
            actor="admin",
        )
    await readiness.run_tenant(TENANT, slot="2026-10-05T07:45:00+00:00")
    builder = registry.day_report_builder()
    builder._readiness = readiness
    return await builder.build(TENANT, "checkout", TODAY, report_id="rep-1", note=NOTE)


def replace_service_clock(registry: ServiceRegistry) -> ReleaseReadinessService:
    """The registry's readiness service, on the report's day and clock."""
    service = registry.release_readiness_service()
    service._clock = lambda: NOW
    service._today = lambda: TODAY
    return service


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for stem, verdict in DAYS.items():
        for name, rendered in formats(asyncio.run(full_report(verdict))).items():
            (out / f"{stem}.{name}").write_bytes(rendered.encode())
    for name, rendered in formats(asyncio.run(readiness_report())).items():
        (out / f"{READINESS_DAY}.{name}").write_bytes(rendered.encode())
