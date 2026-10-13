"""The day's silence ladder on a pod's check-ins, and who reads it (D87, lane R).

The demo fixture: the Runtime Pod holds Asha, Liam and Omar; the Data Pod
Zoe, Ira and Kai. On 15 Jun Asha replied at 09:07 and Liam was asked at 09:00
and never answered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.checkin_rungs import (
    CHECKIN_RUNG_FACT_SOURCE,
    ladder_target,
    rung_fact,
    sent_rungs,
)
from core.domain.escalation import (
    EscalationPolicy,
    EscalationTarget,
    default_escalation_policy,
)
from core.domain.status import CheckInNudge, CheckInNudgeSent
from tests.fixtures.demo_graph import populate_demo_graph

AS_OF = "2026-06-15"
DAY = date(2026, 6, 15)
REMINDED_AT = datetime(2026, 6, 15, 13, 0, tzinfo=UTC)
TOLD_SM_AT = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)


def _as(role: str, user: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    registry = app.state.registry
    tenant = settings.tenant_id
    graph = registry.graph_repository()
    status = registry.status_repository()
    facts = registry.time_series_repository()

    async def seed() -> None:
        await populate_demo_graph(graph, facts, tenant)
        # Liam's reminder went out before rungs were recorded; the scrum master's
        # notice was recorded with whom it was for.
        for number, sent_at in ((1, REMINDED_AT), (2, TOLD_SM_AT)):
            await status.record_checkin_nudge(
                CheckInNudge(
                    tenant_id=tenant,
                    correlation_id="fixture-checkin-liam",
                    nudge_number=number,
                    sent_at=sent_at,
                    outbound_message_id=f"msg-{number}",
                )
            )
        # A rung reserved but never sent is not one.
        await status.record_checkin_nudge(
            CheckInNudge(tenant_id=tenant, correlation_id="fixture-checkin-liam", nudge_number=3)
        )
        await facts.append_fact_once(
            rung_fact(
                tenant_id=tenant,
                pod_id="pod-runtime",
                developer_id="dev-liam",
                correlation_id="fixture-checkin-liam",
                checkin_date=AS_OF,
                nudge_number=2,
                target=EscalationTarget.SCRUM_MASTER,
                action="sent",
                observed_at=TOLD_SM_AT,
            )
        )

    asyncio.run(seed())
    with TestClient(app) as test_client:
        yield test_client


def _person(body: dict, developer_id: str) -> dict:
    return next(item for item in body["developers"] if item["developer_id"] == developer_id)


def test_a_manager_reads_who_was_asked_replied_and_reminded_but_no_check_in_times(
    client: TestClient,
) -> None:
    response = client.get(f"/pods/pod-runtime/checkins?as_of={AS_OF}", headers=_as("mgr", "m"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reads_ladder"] is True
    assert body["reads_schedule"] is False
    liam = _person(body, "dev-liam")
    assert liam["asked_at"].startswith("2026-06-15T09:00")
    assert liam["replied_at"] is None
    assert [(r["number"], r["target"], r["recorded"]) for r in liam["reminders"]] == [
        # Not recorded: today's ladder says rung 1 is the reminder to the person.
        (1, "developer", False),
        (2, "scrum_master", True),
    ]
    assert liam["reminders"][1]["sent_at"].startswith("2026-06-15T17:00")
    assert liam["checkin_time"] is None and liam["timezone"] is None
    asha = _person(body, "dev-asha")
    assert asha["replied_at"].startswith("2026-06-15T09:07")
    assert asha["reminders"] == []


def test_an_admin_also_reads_each_persons_check_in_time_and_zone(client: TestClient) -> None:
    body = client.get(f"/pods/pod-runtime/checkins?as_of={AS_OF}", headers=_as("admin", "a")).json()

    assert body["reads_ladder"] is True and body["reads_schedule"] is True
    for person in body["developers"]:
        assert person["checkin_time"] is not None and len(person["checkin_time"]) == 5
        assert person["timezone"]


def test_a_scrum_master_reads_the_ladder_and_times_of_their_own_pods_only(
    client: TestClient,
) -> None:
    zoe = _as("sm", "dev-zoe")
    own = client.get(f"/pods/pod-data/checkins?as_of={AS_OF}", headers=zoe).json()
    other = client.get(f"/pods/pod-runtime/checkins?as_of={AS_OF}", headers=zoe).json()

    assert own["reads_ladder"] is True and own["reads_schedule"] is True
    # Another pod's board as before, without the ladder or anyone's check-in time.
    assert other["reads_ladder"] is False and other["reads_schedule"] is False
    liam = _person(other, "dev-liam")
    assert liam["reminders"] == [] and liam["asked_at"] is None
    assert liam["checkin_time"] is None


def test_a_developer_reads_their_own_pods_counts_and_only_their_own_row(
    client: TestClient,
) -> None:
    response = client.get(
        f"/pods/pod-runtime/checkins?as_of={AS_OF}", headers=_as("dev", "dev-liam")
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [person["developer_id"] for person in body["developers"]] == ["dev-liam"]
    assert body["reads_ladder"] is False
    assert body["developers"][0]["reminders"] == []
    # The counts are still the pod's: Asha replied.
    assert body["confirmed"] == 1
    assert body["confirmed"] + body["partial"] + body["stale"] + body["missing"] == 3


def test_a_project_reader_gets_the_days_check_ins_as_counts_with_no_one_named(
    client: TestClient,
) -> None:
    for role, who in (("po", "dev-maya"), ("exec", "elena"), ("mgr", "m")):
        response = client.get(
            f"/projects/project-foundations/checkin-coverage?as_of={AS_OF}", headers=_as(role, who)
        )
        assert response.status_code == 200, (role, response.text)
        body = response.json()
        assert body["people"] >= 3
        assert body["coverage"]["replied"] >= 1
        assert "Liam" not in response.text and "Asha" not in response.text
    refused = client.get(
        f"/projects/project-foundations/checkin-coverage?as_of={AS_OF}",
        headers=_as("dev", "dev-liam"),
    )
    assert refused.status_code == 403


# --- the pure reading -------------------------------------------------------------------


def _nudge(number: int, hour: int, correlation: str = "c1") -> CheckInNudgeSent:
    return CheckInNudgeSent(
        developer_id="dev-1",
        correlation_id=correlation,
        nudge_number=number,
        sent_at=datetime(2026, 6, 15, hour, tzinfo=UTC),
    )


def test_a_recorded_rung_says_whom_it_was_for_whatever_todays_ladder_says() -> None:
    no_sm = default_escalation_policy(
        developer_wait_seconds=1,
        scrum_master_wait_seconds=1,
        manager_wait_seconds=1,
        escalate_to_scrum_master=False,
    )
    recorded = rung_fact(
        tenant_id="t",
        pod_id="pod",
        developer_id="dev-1",
        correlation_id="c1",
        checkin_date="2026-06-15",
        nudge_number=2,
        target=EscalationTarget.SCRUM_MASTER,
        action="sent",
        observed_at=datetime(2026, 6, 15, 17, tzinfo=UTC),
    )
    other_day = rung_fact(
        tenant_id="t",
        pod_id="pod",
        developer_id="dev-1",
        correlation_id="c1",
        checkin_date="2026-06-14",
        nudge_number=1,
        target=EscalationTarget.MANAGER,
        action="sent",
        observed_at=datetime(2026, 6, 14, 17, tzinfo=UTC),
    )

    rungs = sent_rungs([_nudge(2, 17), _nudge(1, 13)], [recorded, other_day], no_sm, DAY)

    assert recorded.source == CHECKIN_RUNG_FACT_SOURCE
    assert [(r.number, r.target, r.recorded) for r in rungs["dev-1"]] == [
        (1, EscalationTarget.DEVELOPER, False),
        # Today's ladder has no scrum master rung; the record says it went there.
        (2, EscalationTarget.SCRUM_MASTER, True),
    ]


def test_todays_ladder_names_a_rung_it_has_and_no_other() -> None:
    ladder = default_escalation_policy(
        developer_wait_seconds=1, scrum_master_wait_seconds=1, manager_wait_seconds=1
    )
    assert ladder_target(ladder, 2) is EscalationTarget.SCRUM_MASTER
    assert ladder_target(ladder, 3) is EscalationTarget.MANAGER
    assert ladder_target(ladder, 4) is None
    # Escalation off: the workflow still reminds the person once.
    assert ladder_target(EscalationPolicy(steps=()), 1) is EscalationTarget.DEVELOPER
    assert ladder_target(None, 2) is None


async def test_the_in_memory_store_reads_only_sent_rungs_of_the_days_check_ins() -> None:
    from core.domain.status import CheckIn
    from infra.persistence.in_memory_graph import InMemoryGraphStore

    store = InMemoryGraphStore()
    for correlation, day in (("today", date(2026, 6, 15)), ("yesterday", date(2026, 6, 14))):
        await store.record_checkin(
            CheckIn(
                tenant_id="t",
                developer_id="dev-1",
                correlation_id=correlation,
                asked_at=datetime(2026, 6, 14, 9, tzinfo=UTC),
                replied_at=None,
                raw_reply=None,
                signals=None,
                checkin_date=day,
            )
        )
        await store.record_checkin_nudge(
            CheckInNudge(
                tenant_id="t",
                correlation_id=correlation,
                nudge_number=1,
                sent_at=datetime(2026, 6, 14, 13, tzinfo=UTC),
            )
        )
    await store.record_checkin_nudge(
        CheckInNudge(tenant_id="t", correlation_id="today", nudge_number=2)
    )

    sent = await store.checkin_nudges_on_day("t", date(2026, 6, 15))

    assert [(rung.correlation_id, rung.nudge_number) for rung in sent] == [("today", 1)]


def test_a_rung_fact_is_kept_on_the_pod_with_no_message_text() -> None:
    fact = rung_fact(
        tenant_id="t",
        pod_id="pod-a",
        developer_id="dev-1",
        correlation_id="c1",
        checkin_date="2026-06-15",
        nudge_number=1,
        target=EscalationTarget.DEVELOPER,
        action="suppressed_unavailable",
        observed_at=datetime(2026, 6, 15, 13, tzinfo=UTC),
    )
    # Kept on the pod: the person's own facts are what their check-in reads back.
    assert fact.entity_ref.id == "pod-a"
    assert fact.correlation_id == "checkin-rung:c1:1"
    assert "text" not in fact.payload
