"""The console IA's reads, checks and writes take the tenant's day, not the server's.

The rule of test_tenant_day.py, for the routes and services the console IA added:
a read with no ``as_of`` (or ``day``) answers for ServiceRegistry.tenant_today, an
``as_of`` asked for always wins, and a service that reads links or dates an instant
for the console does it on the tenant's calendar. Each route test pins the tenant's
day three days after the server's, as test_tenant_day.py does: a link made through
the config API starts on the tenant's day, so a read on the server's day misses it,
and a route that echoes its day names the server's.

The services that date an instant are given a zone east of UTC and an instant just
after the tenant's midnight, the hour its UTC date is still the day before.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from api.routers import portfolio_reads
from config.settings import Settings
from core.application import portfolio_delivery_service
from core.application.ask_hygiene import hygiene_payload
from core.application.attention import AttentionView
from core.application.blocker_settlement import BlockerSettlement
from core.application.delivery_scope import MemberScope
from core.application.jira_changes_service import JiraChangesService
from core.application.narrative_brief_service import NarrativeBriefService
from core.application.persona_views import FocusView, PersonaViewService, PortfolioHeatmapView
from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.request_trail_service import RequestTrailService
from core.application.writeback_service import suggestion_correlation
from core.domain.auth import Principal
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.errors import GraphNotFound
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    Verdict,
)
from core.domain.hygiene import HygieneState
from core.domain.risk import DriftFinding
from core.domain.writeback import WriteBackStatus
from infra.registry import ServiceRegistry
from infra.workflows import brief_generation
from tests.unit import test_hygiene_service as hygiene_world
from tests.unit.qa2_checkout_slice import CHK11_BLOCKER, NOAH, ZOE, checkout_slice
from tests.unit.qa2_checkout_slice import DAY as SLICE_DAY
from tests.unit.qa2_checkout_slice import TENANT as SLICE_TENANT
from tests.unit.test_attention import DAY as ATTENTION_DAY
from tests.unit.test_attention import _unanswered_day
from tests.unit.test_blocker_settlement import _request as _request_to
from tests.unit.test_console_consent import _merged_drift
from tests.unit.test_tenant_day import TENANT, _app, _as, _offline, _seed
from tests.unit.test_writeback_reply import _LIAM, _liam_collector
from tests.unit.test_writeback_reply import _TENANT as _LIAM_TENANT

REQUEST_ID = "xreq-tenant-day"
#: East of UTC: from 00:00 to 05:30 there its date is a day after the UTC date.
KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def tenant_day(monkeypatch: pytest.MonkeyPatch) -> date:
    day = date.today() + timedelta(days=3)
    monkeypatch.setattr(ServiceRegistry, "_tenant_today", lambda _self: day)
    return day


def _request(created_at: datetime) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id=REQUEST_ID,
        requester_id="dev-eli",
        requester_chat_ref="dev-eli",
        counterpart_id="dev-ira",
        counterpart_display_name="Ira",
        kind=CrossPersonRequestKind.REVIEW,
        note="Review the wallet change",
        source_correlation_id="checkin-eli",
        status=CrossPersonRequestStatus.OPEN,
        created_at=created_at,
        updated_at=created_at,
    )


def _seed_all(client: TestClient, app: FastAPI) -> None:
    """test_tenant_day's tree, and a request of Eli's to read the trail of."""
    _seed(client, app)
    registry: ServiceRegistry = app.state.registry
    asyncio.run(registry.cross_person_request_repository().create(_request(datetime.now(tz=UTC))))


@pytest.fixture
def seeded(settings: Settings, tenant_day: date) -> Iterator[tuple[TestClient, FastAPI]]:
    app = _app(_offline(settings))
    with TestClient(app) as client:
        _seed_all(client, app)
        yield client, app


def _field(body: object, field: str) -> object:
    """``field`` of ``body``, a dotted path; None where a part on the way is."""
    value = body
    for part in field.split("."):
        if value is None:
            return None
        assert isinstance(value, dict), body
        value = value[part]
    return value


# --- No day asked for: the tenant's ----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "role", "field"),
    [
        ("/projects/checkout/baselines", "admin", "as_of"),
        ("/cross-person-requests/counts", "admin", "as_of"),
        (f"/cross-person-requests/{REQUEST_ID}/trail", "admin", "as_of"),
        ("/projects/checkout/checkin-coverage", "admin", "as_of"),
        ("/projects/checkout/delivery/impact", "admin", "end.day"),
        ("/portfolio/delivery", "admin", "as_of"),
        ("/portfolio/scope", "admin", "as_of"),
        ("/portfolio/warnings", "admin", "as_of"),
        ("/pods/pod-pay/profiles", "admin", "as_of"),
        ("/projects/checkout/timeline", "admin", "axis.today"),
        ("/pods/pod-pay/timeline", "admin", "axis.today"),
        ("/me/conversation", "dev", "day"),
        ("/projects/checkout/asks", "admin", "as_of"),
        ("/me/today-counts", "admin", "as_of"),
        ("/me/jira-changes", "dev", "as_of"),
    ],
)
def test_a_console_read_with_no_day_answers_for_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date, path: str, role: str, field: str
) -> None:
    client, _app = seeded

    response = client.get(path, headers=_as(role))

    assert response.status_code == 200, response.text
    assert _field(response.json(), field) == tenant_day.isoformat()


def test_the_reads_that_echo_no_day_read_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _app = seeded
    asked: list[date] = []
    resolved = RequestTrailService.resolved

    async def recording(
        self: RequestTrailService, principal: Principal, day: date, days: int
    ) -> list[CrossPersonRequest]:
        asked.append(day)
        return await resolved(self, principal, day, days)

    monkeypatch.setattr(RequestTrailService, "resolved", recording)

    done = client.get("/cross-person-requests/resolved")
    mine = client.get("/me/profiles", headers=_as("dev"))

    assert done.status_code == 200, done.text
    assert asked == [tenant_day]
    # Eli joined Payments on the tenant's day: on the server's day he is in no pod.
    assert mine.status_code == 200, mine.text
    assert [item["pod_id"] for item in mine.json()] == ["pod-pay"]


# --- A day asked for: always that day --------------------------------------------------

_PATH_IDS = {
    "program_id": "prog",
    "project_id": "checkout",
    "workstream_id": "ws",
    "pod_id": "pod-pay",
    "request_id": REQUEST_ID,
    "level": "pod",
    "entity_id": "pod-pay",
}
#: Where a read that names its day puts it, when not in a top-level ``as_of``.
_ECHO_FIELDS = {
    "/projects/checkout/timeline": "axis.today",
    "/pods/pod-pay/timeline": "axis.today",
    "/projects/checkout/delivery/impact": "end.day",
    "/persona/pod/pod-pay/trend": "end",
}
#: Developer reads; every other read is asked as an admin.
_DEVELOPER_READS = frozenset({"/me/status", "/me/focus", "/me/delivery-tree", "/me/profiles"})


def _dated_reads(app: FastAPI) -> list[str]:
    """Every GET taking ``as_of``, from the schema, as the console's asOf.test.ts finds them."""
    found = []
    for path, operations in app.openapi()["paths"].items():
        read = operations.get("get")
        if read and any(
            param["in"] == "query" and param["name"] == "as_of"
            for param in read.get("parameters", [])
        ):
            found.append(path.format(**_PATH_IDS))
    return found


def test_an_as_of_asked_for_always_wins(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tree is linked on a past tenant's day, then the tenant's today moves on: a
    # read asked for the past day must answer for it, never for the tenant's today.
    past = date.today() + timedelta(days=1)
    tenant_today = date.today() + timedelta(days=3)
    day = [past]
    monkeypatch.setattr(ServiceRegistry, "_tenant_today", lambda _self: day[0])
    app = _app(_offline(settings))
    with TestClient(app) as client:
        _seed_all(client, app)
        day[0] = tenant_today
        reads = _dated_reads(app)
        wrong: list[str] = []
        echoed = 0
        for path in reads:
            role = "dev" if path in _DEVELOPER_READS else "admin"
            response = client.get(path, params={"as_of": past.isoformat()}, headers=_as(role))
            if response.status_code >= 422:
                wrong.append(f"{path}: {response.status_code} {response.text[:200]}")
                continue
            if response.status_code != 200:
                continue
            body = response.json()
            field = _ECHO_FIELDS.get(path, "as_of")
            said = (
                _field(body, field)
                if isinstance(body, dict) and field.split(".")[0] in body
                else None
            )
            if said is not None:
                echoed += 1
                if said != past.isoformat():
                    wrong.append(f"{path}: {field} is {said}")
            # A read that names no day must not have read the tenant's today either.
            # (One that names it may hold other days, such as a trail's due dates.)
            elif tenant_today.isoformat() in response.text:
                wrong.append(f"{path}: names {tenant_today} {response.text[:200]}")

    assert wrong == []
    # Most dated reads name their day; the directory lists and a few others do not.
    assert len(reads) >= 45
    assert echoed >= 30


# --- No server's day left behind --------------------------------------------------------

_BACKEND = Path(__file__).resolve().parents[2]


class _ServerDay(ast.NodeVisitor):
    """``date.today`` and ``datetime.now(...).date()`` outside a constructor."""

    def __init__(self) -> None:
        self.functions: list[str] = []
        self.found: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and (node.value.id, node.attr) == ("date", "today"):
            self._found(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "date"
            and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Attribute)
            and func.value.func.attr == "now"
            and isinstance(func.value.func.value, ast.Name)
            and func.value.func.value.id == "datetime"
        ):
            self._found(node)
        self.generic_visit(node)

    def _found(self, node: ast.expr) -> None:
        # A constructor's default serves a service built directly (a test); every
        # production construction passes the tenant's today.
        if self.functions[-1:] != ["__init__"]:
            self.found.append(node.lineno)


def test_no_route_or_service_takes_the_servers_day() -> None:
    found = []
    for package in ("api", "core/application"):
        for path in sorted((_BACKEND / package).rglob("*.py")):
            visitor = _ServerDay()
            visitor.visit(ast.parse(path.read_text(), filename=str(path)))
            found += [f"{path.relative_to(_BACKEND)}:{line}" for line in visitor.found]

    # Take the tenant's today: Depends(as_of_or_tenant_today), registry.tenant_today,
    # or a service's today callable.
    assert found == []


# --- Each route family and service on the tenant's day ---------------------------------


def test_a_profile_set_right_after_adding_the_member_is_listed_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date
) -> None:
    client, _app = seeded

    set_ = client.put("/config/pods/pod-pay/members/dev-eli/profile", json={"profile": "qa"})
    listed = client.get("/pods/pod-pay/profiles")

    assert set_.status_code == 200, set_.text
    eli = next(item for item in listed.json()["members"] if item["member_id"] == "dev-eli")
    assert (eli["profile"], eli["checked"]) == ("qa", True)


def test_a_scrum_master_who_joined_today_reads_their_portfolio_and_pod_hygiene(
    seeded: tuple[TestClient, FastAPI],
) -> None:
    client, _app = seeded
    sm = _as("sm")

    delivery = client.get("/portfolio/delivery", headers=sm)
    scope = client.get("/portfolio/scope", headers=sm)
    warnings = client.get("/portfolio/warnings", headers=sm)
    hygiene = client.get("/pods/pod-pay/hygiene", headers=sm)

    for response in (delivery, scope, warnings, hygiene):
        assert response.status_code == 200, response.text
    assert [project["id"] for project in delivery.json()["projects"]] == ["checkout"]
    assert [project["id"] for project in scope.json()["projects"]] == ["checkout"]
    assert warnings.json()["projects"] == 1


def _kept(day: date, p50: date, p85: date) -> ForecastDayRecord:
    return ForecastDayRecord(
        tenant_id=TENANT,
        scope=CommitmentScope(
            kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout"
        ),
        day=day,
        p50=p50,
        p85=p85,
        verdict=Verdict.NO_DATE,
        target_date=None,
        team_latest=None,
        team_latest_key=None,
        remaining=1,
        unit="items",
        sample_days=10,
        completed_in_sample=3,
        min_sample_days=10,
        computed_at=datetime.now(tz=UTC),
    )


def test_the_timelines_and_impact_read_today_live_and_a_past_day_from_kept_rows(
    seeded: tuple[TestClient, FastAPI], tenant_day: date
) -> None:
    client, app = seeded
    yesterday = tenant_day - timedelta(days=1)
    p50, p85 = tenant_day + timedelta(days=20), tenant_day + timedelta(days=30)
    registry: ServiceRegistry = app.state.registry
    asyncio.run(registry.forecast_day_repository().save([_kept(yesterday, p50, p85)]))

    today = client.get("/projects/checkout/timeline").json()
    pod = client.get("/pods/pod-pay/timeline").json()
    impact = client.get("/projects/checkout/delivery/impact").json()
    past = client.get("/projects/checkout/timeline", params={"as_of": yesterday.isoformat()})

    rows = {row["row"]: row for row in today["rows"]}
    # Today is live: no kept row stands in for it, and the pod's band is computed now.
    assert rows["project:checkout"]["forecast"] is None
    assert "working days of history" in rows["project:checkout"]["forecast_reason"]
    assert rows["pod:pod-pay"]["forecast"]["on"] == tenant_day.isoformat()
    assert [row["forecast"]["on"] for row in pod["rows"]] == [tenant_day.isoformat()]
    assert (impact["start"]["day"], impact["start"]["p85"]) == (
        yesterday.isoformat(),
        p85.isoformat(),
    )
    assert (impact["end"]["day"], impact["end"]["p85"]) == (tenant_day.isoformat(), None)
    # A past day asked for reads the row kept that day.
    [kept] = past.json()["rows"]
    assert kept["forecast"] == {
        "p50": p50.isoformat(),
        "p85": p85.isoformat(),
        "on": yesterday.isoformat(),
    }


async def test_jira_changes_are_listed_and_answered_for_the_tenants_day() -> None:
    collector, _tracker, _chat, store = await _liam_collector([])
    write_back = collector._write_back_service
    assert write_back is not None
    now = datetime(2026, 10, 3, 19, 0, tzinfo=UTC)  # 00:30 on 4 Oct in Kolkata
    tenant_day = date(2026, 10, 4)
    asked: list[tuple[str, date]] = []

    async def project_drift(tenant_id: str, project_id: str, day: date) -> list[DriftFinding]:
        asked.append(("drift", day))
        return [replace(_merged_drift(), detected_at=now)]

    async def scope_of(tenant_id: str, subject: str) -> MemberScope:
        return MemberScope(projects=frozenset({"checkout"}))

    async def focus_of(tenant_id: str, subject: str, day: date) -> FocusView:
        asked.append(("focus", day))
        raise GraphNotFound("no tasks")

    service = JiraChangesService(
        write_back=write_back,
        status_repository=store,
        graph_repository=store,
        identity_link_repository=store,
        project_drift=project_drift,
        scope_of=scope_of,
        focus_of=focus_of,
        finisher=lambda: collector,
        tracker_name="the issue tracker",
        clock=lambda: now,
        today=lambda: tenant_day,
    )

    listed = await service.changes(_LIAM_TENANT, _LIAM)
    answer = await service.answer(_LIAM_TENANT, _LIAM, "suggestion:CHK-3", "no")

    assert listed.as_of == tenant_day
    assert {day for _what, day in asked} == {tenant_day}
    assert answer.outcome == "declined"
    [row] = await store.list_writeback_by_correlation(
        _LIAM_TENANT, suggestion_correlation("CHK-3", tenant_day)
    )
    assert row.status is WriteBackStatus.DECLINED
    assert (
        await store.list_writeback_by_correlation(
            _LIAM_TENANT, suggestion_correlation("CHK-3", now.date())
        )
        == []
    )


def test_an_executive_reads_the_brief_of_a_pod_of_five_linked_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI],
) -> None:
    client, app = seeded
    for member in ("dev-ada", "dev-ben", "dev-cleo", "dev-dina"):
        assert client.post("/config/members", json={"id": member, "name": member}).status_code
        assert (
            client.post(
                f"/config/pods/pod-pay/members/{member}", json={"role": "engineer"}
            ).status_code
            == 200
        )
    registry: ServiceRegistry = app.state.registry
    asyncio.run(
        registry.narrative_brief_repository().record_brief(
            NarrativeBrief(
                tenant_id=TENANT,
                kind=BriefKind.DAILY_POD,
                scope_id="pod-pay",
                title="Payments today",
                body="Card payments moved to review.",
                generated_at=datetime.now(tz=UTC),
            )
        )
    )

    briefs = client.get("/persona/briefs", params={"kind": "daily_pod"}, headers=_as("exec"))

    # Five people on the tenant's day: not a small group, so the executive reads it.
    assert briefs.status_code == 200, briefs.text
    assert [brief["scope_id"] for brief in briefs.json()["briefs"]] == ["pod-pay"]


def test_code_work_reads_profiles_and_pod_links_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI],
) -> None:
    client, app = seeded
    assert (
        client.put(
            "/config/pods/pod-pay/members/dev-eli/profile", json={"profile": "development"}
        ).status_code
        == 200
    )
    registry: ServiceRegistry = app.state.registry

    decided = asyncio.run(
        registry.status_collector()._code_work_from_profiles(TENANT, "dev-eli", ["CHK-1"])
    )

    assert decided == {"CHK-1": True}


def test_a_conversation_day_past_the_window_from_the_tenants_today_is_not_kept(
    seeded: tuple[TestClient, FastAPI], tenant_day: date, settings: Settings
) -> None:
    client, _app = seeded
    window = settings.conversation_retention_days
    edge = tenant_day - timedelta(days=window)

    kept = client.get("/me/conversation", params={"day": edge.isoformat()}, headers=_as("dev"))
    gone = client.get(
        "/me/conversation",
        params={"day": (edge - timedelta(days=1)).isoformat()},
        headers=_as("dev"),
    )

    assert (kept.json()["day"], kept.json()["kept"]) == (edge.isoformat(), True)
    assert gone.json()["kept"] is False


def test_exec_attention_calls_the_tenants_live_day_today_for_a_reader_ahead(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tenant's today is the attention day; the reader's own date is days later.
    # Whether the day is live is the tenant's calendar, as on the heat map.
    monkeypatch.setattr(ServiceRegistry, "_tenant_today", lambda _self: ATTENTION_DAY)
    store = asyncio.run(_unanswered_day())
    app = create_app(settings=settings, registry=ServiceRegistry(settings, graph_store=store))
    with TestClient(app) as client:
        response = client.get(
            "/portfolio/attention",
            params={"program_root_id": "prog", "tz": "Pacific/Kiritimati"},
        )

    assert response.status_code == 200, response.text
    headline = response.json()["headline"]
    # The live day's words; on the reader's later date it was the past day's.
    assert "has answered today's check-in yet" in headline
    assert f"{ATTENTION_DAY.day} {ATTENTION_DAY:%b}" not in headline


async def test_a_wait_resolved_after_the_tenants_midnight_resolves_the_blocker_that_day() -> None:
    store = await checkout_slice()
    to_noah = await store.create(
        _request_to("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )
    # 19:00 UTC on the slice's day is 00:30 the next day in Kolkata.
    resolved_at = datetime.combine(SLICE_DAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=19)
    local_day = SLICE_DAY + timedelta(days=1)

    settled = await BlockerSettlement(store, zone=KOLKATA).settle_for_request(
        to_noah, resolved_at=resolved_at
    )

    assert [(row.blocker_id, row.resolved_on) for row in settled] == [("blk-chk8", local_day)]
    # The status of that day or before is revised: CHK-8 no longer blocks Zoe.
    revised = await store.latest_developer_status(SLICE_TENANT, ZOE, local_day)
    assert revised is not None and revised.blockers == (CHK11_BLOCKER,)


class _RecordingViews(PersonaViewService):
    """The persona reads the brief makes, with the day each is for and its today."""

    days: list[tuple[str, date, object]]

    async def portfolio_heatmap(  # type: ignore[override]
        self, tenant_id: str, as_of: date, *args: object, **kw: object
    ) -> PortfolioHeatmapView:
        self.days.append(("heatmap", as_of, kw.get("today")))
        return await super().portfolio_heatmap(tenant_id, as_of, *args, **kw)  # type: ignore[arg-type]

    async def portfolio_attention(  # type: ignore[override]
        self, tenant_id: str, as_of: date, *args: object, **kw: object
    ) -> AttentionView:
        self.days.append(("attention", as_of, kw.get("today")))
        return await super().portfolio_attention(tenant_id, as_of, *args, **kw)  # type: ignore[arg-type]


async def test_the_exec_brief_is_about_its_runs_day_in_the_tenants_zone(
    settings: Settings,
) -> None:
    registry = ServiceRegistry(_offline(settings))
    store = registry.graph_repository()
    views = _RecordingViews(
        graph_repository=store,
        status_repository=registry.status_repository(),
        rollup_repository=registry.rollup_repository(),
        time_series_repository=registry.time_series_repository(),
    )
    views.days = []
    service = NarrativeBriefService(
        llm_provider=registry.llm_provider(),
        feed_service=PortfolioFeedService(registry.time_series_repository()),
        persona_view_service=views,
        graph_repository=store,
        brief_repository=registry.narrative_brief_repository(),
        model="test-model",
        status_repository=registry.status_repository(),
        zone=KOLKATA,
    )
    run_at = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)  # 00:30 on 14 Oct in Kolkata
    brief_day = date(2026, 10, 14)

    await service.facts(TENANT, BriefKind.EXEC, "", as_of=run_at)

    assert views.days
    assert {(as_of, today) for _read, as_of, today in views.days} == {(brief_day, brief_day)}


def test_the_registry_and_the_reads_date_instants_in_the_tenants_zone(
    settings: Settings,
) -> None:
    registry = ServiceRegistry(
        _offline(settings).model_copy(update={"tenant_default_timezone": "Asia/Kolkata"})
    )
    zone = registry.tenant_zone()

    assert zone == KOLKATA
    assert brief_generation._brief_service(registry)._zone == zone
    settlement = registry.cross_person_request_service().blocker_settlement
    assert settlement is not None and settlement._zone == zone
    assert registry.hygiene_service()._zone == zone
    assert registry.forecast_service().zone() == zone
    assert portfolio_reads.get_portfolio_scope_service(registry)._zone == zone
    assert portfolio_reads.get_portfolio_delivery_service(registry)._zone == zone
    warnings = portfolio_reads.get_portfolio_warnings_service(registry, registry.risk_service())
    assert warnings._zone == zone


async def test_hygiene_counts_a_finding_first_seen_after_the_tenants_midnight_as_new_today() -> (
    None
):
    world = await hygiene_world._world()
    world.service._zone = KOLKATA
    # 00:30 on the world's today in Kolkata; its UTC date is the day before.
    # The clock is UTC, as a scan's and Postgres's timestamps are.
    world.now = (
        datetime.combine(hygiene_world.TODAY, datetime.min.time(), tzinfo=KOLKATA)
        + timedelta(minutes=30)
    ).astimezone(UTC)
    await world.rule(hygiene_world.NO_POINTS)
    await world.run()

    board = await world.service.project_board(
        hygiene_world.TENANT, "checkout", hygiene_world.MANAGER
    )

    [row] = board.rules
    assert (row.open, row.new_today, row.oldest_days) == (1, 1, 0)
    assert [view.open_days for view in board.findings] == [0]
    assert all(view.finding.state is HygieneState.OPEN for view in board.findings)


def test_a_committed_date_change_counts_and_marks_on_the_tenants_day() -> None:
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")
    day = date(2026, 10, 13)
    # 00:30 on 14 Oct in Kolkata, still 13 Oct in UTC.
    changed_at = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)
    changes = [
        DateChange(
            tenant_id=TENANT,
            scope=scope,
            target_date=date(2026, 11, 20),
            changed_by="dev-po",
            changed_at=changed_at - timedelta(days=10),
            note=None,
        ),
        DateChange(
            tenant_id=TENANT,
            scope=scope,
            target_date=date(2026, 11, 27),
            changed_by="dev-po",
            changed_at=changed_at,
            note=None,
        ),
    ]

    by_day = portfolio_delivery_service._changes_until(changes, day, KOLKATA)
    by_next = portfolio_delivery_service._changes_until(changes, day + timedelta(days=1), KOLKATA)
    marks = portfolio_delivery_service._bar_marks(
        [], by_next[scope.key], day - timedelta(days=30), day + timedelta(days=1), KOLKATA
    )

    assert [change.target_date for change in by_day[scope.key]] == [date(2026, 11, 20)]
    assert [change.target_date for change in by_next[scope.key]] == [
        date(2026, 11, 20),
        date(2026, 11, 27),
    ]
    assert [(mark.day, mark.kind) for mark in marks] == [
        (day + timedelta(days=1), "committed_date_changed")
    ]


def test_the_scan_day_ora_reads_is_the_tenants() -> None:
    as_of = date(2026, 10, 13)
    board = SimpleNamespace(
        rules=[],
        findings=[],
        counts_only=True,
        scope_name="Checkout",
        settings=SimpleNamespace(enabled=True),
        jira=SimpleNamespace(as_of=None, stale=False),
        # 00:30 on 14 Oct in Kolkata.
        last_run=SimpleNamespace(finished_at=datetime(2026, 10, 13, 19, 0, tzinfo=UTC)),
    )

    in_utc = hygiene_payload(board, as_of)  # type: ignore[arg-type]
    in_kolkata = hygiene_payload(board, as_of, zone=KOLKATA)  # type: ignore[arg-type]

    assert "history" not in in_utc
    assert in_kolkata["history"] == (
        "Jira hygiene keeps no history: this is the scan of 2026-10-14, which may not be "
        "how it stood on 2026-10-13."
    )
