"""A status given in the console is the day's answer to its check-in.

A task update, a confirm and a correction all count, by one rule
(``status_summaries.answered_in_console``): no nudge or escalation goes out
for the day, and its close-out leaves that status as it is instead of writing
an inferred, stale or unknown one over it. The check-in still closes, so a
chat reply after the close-out is a late update (G9).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from core.application.self_status_service import SelfStatusService
from core.application.status_collector import StatusCollector
from core.application.status_summaries import TASK_UPDATE_LEAD, answered_in_console
from core.application.task_update_service import TaskUpdate, TaskUpdateService
from core.domain.escalation import EscalationTarget
from core.domain.status import CheckIn, CheckInCorrelation, DeveloperStatus, StatusSource
from core.domain.writeback import WriteBackTarget
from core.ports.chat import ChatProvider
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import nudge
from tests.contract.fakes import FakeChatProvider
from tests.unit.test_task_update import _DEV, _TENANT, _TODAY, _team

_CORRELATION = "checkin-kai-1"
_ASKED_AT = datetime(2026, 10, 8, 7, 30, tzinfo=UTC)


class _Registry(ServiceRegistry):
    """Memory mode with a recording chat; the ladder's activities never close it."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            settings.model_copy(
                update={
                    "chat_provider": "fake",
                    "llm_provider": "fake",
                    "issue_tracker_provider": "fake",
                    "calendar_provider": "fake",
                    "vcs_provider": "fake",
                }
            )
        )
        self.chat = FakeChatProvider()

    def chat_provider(self) -> ChatProvider:
        return self.chat

    @property
    def store(self) -> InMemoryGraphStore:
        return self._memory_graph_store()

    async def close(self) -> None:
        return None


@pytest.fixture
async def registry(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> _Registry:
    """Kai's team, and today's check-in sent to him and not answered in chat."""
    registry = _Registry(settings)
    await _team(registry.store)
    await registry.store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_DEV,
            correlation_id=_CORRELATION,
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_TODAY,
        )
    )
    await registry.store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            correlation_id=_CORRELATION,
            developer_id=_DEV,
            chat_user_ref=_DEV,
            chat_thread_ref="D-kai",
            outbound_message_id="out-1",
            asked_at=_ASKED_AT,
        )
    )
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    return registry


def _ladder() -> nudge.NudgeInput:
    return nudge.NudgeInput(
        tenant_id=_TENANT,
        correlation_id=_CORRELATION,
        as_of=_TODAY.isoformat(),
        developer_name="Kai Thompson",
        chat_external_id=_DEV,
    )


async def _rung(target: EscalationTarget = EscalationTarget.DEVELOPER) -> nudge.NudgeResult:
    return await nudge.send_escalation_step_activity(_ladder().step_input(1, target.value))


async def _task_update(registry: _Registry) -> DeveloperStatus:
    service = TaskUpdateService(
        graph_repository=registry.store,
        status_repository=registry.store,
        time_series_repository=registry.store,
        today=lambda: _TODAY,
    )
    result = await service.update(
        _TENANT,
        _DEV,
        "CHK-4",
        _TODAY,
        TaskUpdate(state=WriteBackTarget.IN_REVIEW, note="MR !3 open"),
    )
    return result.status


async def test_a_console_task_update_is_the_days_answer_to_its_check_in(
    registry: _Registry,
) -> None:
    partial = await _task_update(registry)

    rung = await _rung()
    legacy_nudge = await nudge.send_checkin_nudge_activity(_ladder())
    escalation = await _rung(EscalationTarget.SCRUM_MASTER)
    closed = await nudge.close_checkin_non_response_activity(_ladder())

    # No nudge and no escalation went out: the person had answered.
    assert (rung.status, legacy_nudge.status, escalation.status) == (
        nudge.SUPPRESSED_REPLIED,
        nudge.SUPPRESSED_REPLIED,
        nudge.SUPPRESSED_REPLIED,
    )
    assert registry.chat.sent == []
    assert await registry.store.checkin_nudge_for(_TENANT, _CORRELATION, 1) is None
    # The close-out left the partial status exactly as the update wrote it.
    assert (closed.status, closed.terminal_source) == ("closed", StatusSource.PARTIAL.value)
    after = await registry.store.latest_developer_status(_TENANT, _DEV, _TODAY)
    assert after == partial
    assert after is not None and after.summary.startswith(TASK_UPDATE_LEAD)
    # The check-in itself is closed, so a chat reply now is a late update (G9).
    correlation = await registry.store.checkin_correlation_by_id(_TENANT, _CORRELATION)
    assert correlation is not None and correlation.consumed_at is not None


async def test_without_a_console_answer_the_ladder_nudges_and_the_close_out_infers(
    registry: _Registry,
) -> None:
    rung = await _rung()
    closed = await nudge.close_checkin_non_response_activity(_ladder())

    assert rung.status == "nudged"
    assert len(registry.chat.sent) == 1
    assert closed.terminal_source in {
        StatusSource.INFERRED.value,
        StatusSource.STALE.value,
        StatusSource.UNKNOWN.value,
    }


@pytest.mark.parametrize("action", ["confirm", "correct"])
async def test_confirm_and_correct_answer_the_check_in_by_the_same_rule(
    registry: _Registry, action: str
) -> None:
    store = registry.store
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_DEV,
            as_of=date(2026, 10, 7),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Reviewed the payment intent API.",
        )
    )
    service = SelfStatusService(store)
    if action == "confirm":
        given = await service.confirm(_TENANT, _DEV, _TODAY)
    else:
        given = await service.correct(
            _TENANT, _DEV, _TODAY, summary="Pairing on CHK-4.", blockers=(), eta_change_days=None
        )

    rung = await _rung()
    closed = await nudge.close_checkin_non_response_activity(_ladder())

    assert rung.status == nudge.SUPPRESSED_REPLIED
    assert registry.chat.sent == []
    assert closed.terminal_source == StatusSource.CONFIRMED.value
    assert await store.latest_developer_status(_TENANT, _DEV, _TODAY) == given


async def test_the_collector_reads_the_rule_for_the_day_asked(
    registry: _Registry,
) -> None:
    """The console answer counts for its own day only."""
    await _task_update(registry)
    collector: StatusCollector = registry.status_collector()
    assert await collector.answered_in_console(_TENANT, _DEV, _TODAY)
    # The day after, the same partial status no longer answers anything.
    assert not await collector.answered_in_console(_TENANT, _DEV, date(2026, 10, 9))


def _status(source: StatusSource, summary: str, *, confirmed: bool = False) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id=_TENANT,
        developer_id=_DEV,
        as_of=_TODAY,
        source=source,
        blockers=(),
        summary=summary,
        developer_confirmed=confirmed,
    )


def test_the_rule_reads_the_days_status_only() -> None:
    task_update = _status(StatusSource.PARTIAL, f"{TASK_UPDATE_LEAD} CHK-4 in review.")
    confirmed = _status(StatusSource.CONFIRMED, "Shipped it.", confirmed=True)
    chat_partial = _status(StatusSource.PARTIAL, "Working on CHK-4; ETA not given.")
    inferred = _status(StatusSource.INFERRED, "No confirmed check-in after a nudge. Inferred.")

    assert answered_in_console(task_update, _TODAY)
    assert answered_in_console(confirmed, _TODAY)
    assert not answered_in_console(chat_partial, _TODAY)
    assert not answered_in_console(inferred, _TODAY)
    assert not answered_in_console(None, _TODAY)
    assert not answered_in_console(task_update, date(2026, 10, 9))
