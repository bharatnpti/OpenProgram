"""Step definitions for schedule/fanout/idempotency scenarios.

Covers MS-E2E-003 (compose cron override), MS-E2E-015 (fanout dispatches
missing eligible members), MS-E2E-016 (idempotent same member/date dispatch)
and MS-E2E-017 (stale skipped_weekend run blocks rerun) by calling the real
``infra.workflows.daily_checkin`` / ``infra.workflows.checkin_fanout``
activities directly against a stable in-memory registry, mirroring the
pattern already used in ``backend/tests/unit/test_agent_and_workflow.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from pytest_bdd import given, parsers, then, when

from core.domain.status import CheckInScheduleRun
from core.domain.workflows import CheckinFanoutInput
from infra.workflows import checkin_fanout, daily_checkin
from tests.bdd.fixtures import World

ROOT = Path(__file__).resolve().parents[3]


@given("a check-in workflow registry backed by the mock Slack simulator")
def _given_fanout_registry(world: World, monkeypatch: object) -> None:
    world.start_app()
    registry = world.registry()
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)  # type: ignore[attr-defined]
    monkeypatch.setattr(checkin_fanout, "_service_registry", lambda: registry)  # type: ignore[attr-defined]


@when(
    parsers.parse('I run the check-in fanout dispatch for tenant "{tenant_id}" on "{checkin_date}"')
)
def _when_run_fanout(world: World, tenant_id: str, checkin_date: str) -> None:
    result = asyncio.run(
        checkin_fanout.dispatch_checkins_for_tenant_activity(
            CheckinFanoutInput(tenant_id=tenant_id, checkin_date=checkin_date)
        )
    )
    world.stash["fanout_result"] = result


@then(parsers.parse("the fanout result dispatched count should be {count:d}"))
def _then_fanout_dispatched_count(world: World, count: int) -> None:
    result = world.stash["fanout_result"]
    assert result.dispatched == count, result


@when(
    parsers.parse(
        'I run the daily check-in activity for developer "{developer_id}" on "{checkin_date}"'
    )
)
def _when_run_daily_checkin_activity(world: World, developer_id: str, checkin_date: str) -> None:
    result = asyncio.run(
        daily_checkin.start_daily_checkin_activity(
            daily_checkin.DailyCheckinInput(
                tenant_id="demo",
                developer_id=developer_id,
                developer_name=developer_id,
                chat_external_id=developer_id,
                checkin_date=checkin_date,
            )
        )
    )
    world.stash.setdefault("daily_checkin_results", []).append(result)
    world.stash["daily_checkin_result"] = result


@then(parsers.parse('the daily check-in result status should be "{status}"'))
def _then_daily_checkin_status(world: World, status: str) -> None:
    result = world.stash["daily_checkin_result"]
    assert result.status == status, result


@then("the daily check-in result should be marked already recorded")
def _then_daily_checkin_already_recorded(world: World) -> None:
    result = world.stash["daily_checkin_result"]
    assert result.already_recorded is True, result


@then("the daily check-in result should not be marked already recorded")
def _then_daily_checkin_not_already_recorded(world: World) -> None:
    result = world.stash["daily_checkin_result"]
    assert result.already_recorded is False, result


@then(parsers.parse("no simulator bot message should have been sent"))
def _then_no_simulator_message(world: World) -> None:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/messages")
    assert response.json()["items"] == []


@given(
    parsers.parse(
        'developer "{developer_id}" has a "{status}" schedule run recorded for "{checkin_date}"'
    )
)
def _given_existing_schedule_run(
    world: World,
    developer_id: str,
    status: str,
    checkin_date: str,
) -> None:
    registry = world.registry()
    reason = "check-in preference excludes this weekday" if status == "skipped_weekend" else None
    asyncio.run(
        registry.status_repository().record_checkin_schedule_run(
            CheckInScheduleRun(
                tenant_id="demo",
                developer_id=developer_id,
                checkin_date=date.fromisoformat(checkin_date),
                correlation_id=f"checkin-{developer_id}-{checkin_date}",
                status=status,
                scheduled_at=datetime.fromisoformat(f"{checkin_date}T09:30:00+00:00").astimezone(
                    UTC
                ),
                reason=reason,
            )
        )
    )


@given("the compose file is loaded")
def _given_compose_file_loaded(world: World) -> None:
    world.stash["compose"] = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )


@then(parsers.parse('the "{service}" service check-in fanout cron is overrideable via "{env_var}"'))
def _then_service_fanout_cron_overrideable(world: World, service: str, env_var: str) -> None:
    compose = world.stash["compose"]
    environment = compose["services"][service]["environment"]
    value = environment.get("PULSEOPS_CHECKIN_FANOUT_CRON")
    assert value is not None
    assert env_var in value, value
