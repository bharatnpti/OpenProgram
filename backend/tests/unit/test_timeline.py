"""The timeline per project and pod: its rows, who sees which, its markers and its words.

The made-up month of test_delivery_impact.py: Checkout Revamp, planned on Thu
1 Oct with the change label "CR"; CHK-20 joined on 6 Oct with the label and
CHK-21 on 8 Oct without it; the committed date was set on 20 Sep and moved on
7 Oct; CHK-4 is open past its due date of 2 Oct. Payments Pod is run by Dana
and has Asha as its developer; Storefront Pod has nobody of theirs. The kept
forecast days run to Fri 9 Oct; today, Mon 12 Oct, has none kept yet.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.application.impact_service import ImpactPeriod, PeriodKind
from core.domain.auth import Role
from core.domain.blockers import BlockerResolutionReason, BlockerSource, DeveloperBlocker
from core.domain.delivery import DEFAULT_STAGE_MAPPING
from core.domain.errors import AuthorizationDenied
from core.domain.forecast import ReleaseMatch, ReleaseMatchKind, Verdict
from core.domain.graph import Task
from core.domain.hygiene import HygieneFinding, HygieneState
from core.domain.impact import Measure
from core.domain.release_readiness import ScopeKind, ScopeRef
from core.domain.timeline import SentenceParts, axis, no_forecast_reason, row_sentence, worst_first
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.unit.test_delivery_impact import (
    PAY,
    PLANNED,
    PROJECT,
    TENANT,
    TODAY,
    _as,
    _client,
    _contains,
    _principal,
    _reconstructed,
    _world,
)

FRIDAY = date(2026, 10, 9)


def _markers(row: object) -> dict[tuple[date, str], object]:
    return {(marker.day, marker.kind): marker for marker in row.markers}  # type: ignore[attr-defined]


async def _blockers(store: InMemoryGraphStore) -> None:
    """A blocker on CHK-2 from 5 Oct, resolved on 8 Oct, and one on CHK-3 still open."""
    for blocker_id, key, opened, resolved in (
        ("blk-1", "CHK-2", date(2026, 10, 5), date(2026, 10, 8)),
        ("blk-2", "CHK-3", date(2026, 10, 7), None),
    ):
        store._developer_blockers[(TENANT, blocker_id)] = DeveloperBlocker(
            tenant_id=TENANT,
            blocker_id=blocker_id,
            developer_id="dev-asha",
            description="Waiting on   the sandbox keys from the payments vendor",
            normalized_key=blocker_id,
            work_item_id=key,
            source=BlockerSource.CHECKIN,
            first_seen_on=opened,
            last_seen_on=resolved or TODAY,
            resolved_on=resolved,
            resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED if resolved else None,
        )


# --- The rows a project's timeline lists ------------------------------------------------


async def test_a_product_owner_reads_the_project_row_then_the_pods_worst_first() -> None:
    registry, _store = await _reconstructed()

    view = await registry.timeline_service().project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", as_of=TODAY
    )

    assert [row.row_id for row in view.rows][0] == "project:checkout"
    assert {row.row_id for row in view.rows[1:]} == {"pod:pod-pay", "pod:pod-store"}
    pods = view.rows[1:]
    assert list(pods) == sorted(pods, key=lambda row: worst_first(row.verdict, row.name))
    project = view.rows[0]
    assert project.level.value == "panel"
    assert project.baseline_day == PLANNED and project.start == PLANNED
    assert project.committed == date(2026, 11, 27)
    assert [(tick.target, tick.on) for tick in project.committed_history] == [
        (date(2026, 11, 20), date(2026, 9, 20)),
        (date(2026, 11, 27), date(2026, 10, 7)),
    ]
    # Today has no kept row yet: the band is the one kept last, on Friday.
    assert project.forecast_on == FRIDAY
    assert project.p50 is not None and project.p85 is not None
    assert project.done is not None and project.total is not None and project.unit
    assert project.at_baseline is not None and project.in_scope is not None
    assert project.in_scope - project.at_baseline == 2
    assert project.can_set_date is True
    assert view.axis.today == TODAY and view.axis.start == PLANNED
    assert view.axis.end > date(2026, 11, 27)
    assert view.change_label == "CR"


async def test_the_project_row_marks_what_happened_on_the_day_it_happened() -> None:
    registry, _store = await _reconstructed()

    view = await registry.timeline_service().project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", as_of=TODAY
    )
    markers = _markers(view.rows[0])

    labelled = markers[(date(2026, 10, 6), "scope_added")]
    unlabelled = markers[(date(2026, 10, 8), "scope_added")]
    assert labelled.change_label is True and [i.key for i in labelled.items] == ["CHK-20"]
    assert unlabelled.change_label is False and [i.key for i in unlabelled.items] == ["CHK-21"]
    assert unlabelled.items[0].added_by == "Asha Rao"
    assert unlabelled.items[0].change_label is False
    # Jira hygiene has nothing open on it: read, and not ringed.
    assert unlabelled.items[0].hygiene is False
    # A requirement that carries the label is never ringed.
    assert labelled.items[0].hygiene is None
    assert markers[(date(2026, 10, 7), "committed_date_changed")].count == 1
    assert [i.key for i in markers[(date(2026, 10, 3), "late")].items] == ["CHK-4"]
    # A product owner does not read the pods' blockers.
    assert not any(kind.startswith("blocker") for _day, kind in markers)
    sentence = view.rows[0].sentence
    assert sentence.startswith("Checkout Revamp: ")
    assert "2 requirements added since planning, 1 without the CR label" in sentence
    assert "the committed date moved once, 20 Nov → 27 Nov" in sentence


async def test_the_timeline_reads_kept_rows_only_and_never_runs_a_forecast() -> None:
    registry, _store = await _reconstructed()
    service = registry.timeline_service()
    forecasts = service._forecasts

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a forecast was simulated on a timeline read")

    forecasts.project_delivery = refuse  # type: ignore[method-assign]
    forecasts.forecast_day = refuse  # type: ignore[method-assign]
    forecasts.scope_delivery = refuse  # type: ignore[method-assign]

    view = await service.project_timeline(_principal(Role.MGR), "checkout", as_of=TODAY)
    pod = await service.pod_timeline(_principal(Role.MGR), "pod-pay", as_of=TODAY)

    assert view.rows and pod.rows


async def test_a_manager_reads_the_blockers_opened_and_closed_with_what_they_wait_on() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)

    view = await registry.timeline_service().project_timeline(
        _principal(Role.MGR), "checkout", as_of=TODAY
    )
    project = _markers(view.rows[0])
    pay = _markers(next(row for row in view.rows if row.row_id == "pod:pod-pay"))

    opened = project[(date(2026, 10, 5), "blocker_opened")]
    assert [item.key for item in opened.items] == ["CHK-2"]
    assert opened.items[0].waits_on == "Waiting on the sandbox keys from the payments vendor"
    assert (date(2026, 10, 8), "blocker_closed") in project
    assert (date(2026, 10, 7), "blocker_opened") in pay
    # Markers never name who raised the blocker.
    assert all(item.added_by is None for item in opened.items)


async def test_an_executive_reads_every_row_without_blockers_or_who_added_an_item() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)

    view = await registry.timeline_service().project_timeline(
        _principal(Role.EXEC), "checkout", as_of=TODAY
    )

    assert all(row.level.value == "panel" for row in view.rows)
    for row in view.rows:
        assert not any(marker.kind.startswith("blocker") for marker in row.markers)
        assert all(item.added_by is None for marker in row.markers for item in marker.items)
        assert row.can_set_date is False


async def test_a_scrum_master_reads_their_pod_whole_and_the_others_dates_only() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)

    view = await registry.timeline_service().project_timeline(
        _principal(Role.SM, "dev-dana"), "checkout", as_of=TODAY
    )
    rows = {row.row_id: row for row in view.rows}

    assert set(rows) == {"pod:pod-pay", "pod:pod-store"}  # no project row
    pay, other = rows["pod:pod-pay"], rows["pod:pod-store"]
    assert pay.level.value == "panel" and pay.can_set_date is True
    assert any(marker.kind == "blocker_opened" for marker in pay.markers)
    assert pay.done is not None and pay.reference == date(2026, 11, 27)
    assert other.level.value == "dates" and other.can_set_date is False
    assert other.markers == () and other.done is None and other.at_baseline is None
    assert "planning" not in other.sentence


async def test_a_developer_reads_only_their_own_pods_row_and_no_project() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)
    service = registry.timeline_service()
    asha = _principal(Role.DEV, "dev-asha")

    with pytest.raises(AuthorizationDenied):
        await service.project_timeline(asha, "checkout", as_of=TODAY)
    with pytest.raises(AuthorizationDenied):
        await service.pod_timeline(asha, "pod-store", as_of=TODAY)
    view = await service.pod_timeline(asha, "pod-pay", as_of=TODAY)

    (row,) = view.rows
    assert row.level.value == "panel" and row.reference == date(2026, 11, 27)
    assert any(marker.kind == "blocker_closed" for marker in row.markers)
    assert all(item.added_by is None for marker in row.markers for item in marker.items)
    assert row.can_set_date is False


async def test_an_addition_without_the_label_is_ringed_while_jira_hygiene_has_it_open() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)
    findings = registry.hygiene_repository()
    seen = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    for finding_id, key, project, state in (
        ("hf-1", "CHK-21", "checkout", HygieneState.OPEN),
        # Resolved, or about another project's issue: no ring.
        ("hf-2", "CHK-20", "checkout", HygieneState.RESOLVED),
        ("hf-3", "WEB-9", "web", HygieneState.OPEN),
    ):
        await findings.save_finding(
            HygieneFinding(
                tenant_id=TENANT,
                finding_id=finding_id,
                rule_id="rule-label",
                rule_version=1,
                issue_key=key,
                scope=ScopeRef(kind=ScopeKind.PROJECT, id=project),
                project_id=project,
                state=state,
                owner_member_id="dev-mina",
                detail={"what": "Added after planning without the change label"},
                fingerprint=finding_id,
                held=False,
                first_seen_at=seen,
                last_seen_at=seen,
            )
        )
    service = registry.timeline_service()

    def unlabelled(view: object) -> object:
        return _markers(view.rows[0])[(date(2026, 10, 8), "scope_added")].items[0]  # type: ignore[attr-defined]

    for role, subject in ((Role.PO, "dev-mina"), (Role.MGR, "someone")):
        view = await service.project_timeline(_principal(role, subject), "checkout", as_of=TODAY)
        assert unlabelled(view).hygiene is True  # type: ignore[attr-defined]
    # An executive reads hygiene's counts, never its issues: no ring.
    view = await service.project_timeline(_principal(Role.EXEC), "checkout", as_of=TODAY)
    assert unlabelled(view).hygiene is None  # type: ignore[attr-defined]
    # A developer reads no hygiene: their pod's row rings nothing.
    (row,) = (
        await service.pod_timeline(_principal(Role.DEV, "dev-asha"), "pod-pay", as_of=TODAY)
    ).rows
    assert all(item.hygiene is None for marker in row.markers for item in marker.items)


async def test_a_past_day_is_read_as_it_stood_that_day() -> None:
    registry, _store = await _reconstructed()
    tuesday = date(2026, 10, 6)

    view = await registry.timeline_service().project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", as_of=tuesday
    )
    project = view.rows[0]
    kept = await registry.forecast_day_repository().list_between(TENANT, PROJECT, tuesday, tuesday)

    assert view.axis.today == tuesday and project.forecast_on == tuesday
    assert (project.p50, project.p85) == (kept[0].p50, kept[0].p85)
    assert project.committed == date(2026, 11, 20)  # the move came on 7 Oct
    assert [tick.on for tick in project.committed_history] == [date(2026, 9, 20)]
    assert {marker.day for marker in project.markers} <= {
        date(2026, 10, 3),
        date(2026, 10, 6),
    }


async def test_without_a_baseline_a_row_starts_at_its_first_kept_day_and_says_so() -> None:
    registry, _store = await _reconstructed()
    repository = registry.planning_baseline_repository()
    repository._baselines.clear()  # type: ignore[attr-defined]

    view = await registry.timeline_service().project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", as_of=TODAY
    )
    project = view.rows[0]

    assert project.baseline_day is None and project.at_baseline is None
    assert project.start < PLANNED
    assert "planning is not marked complete yet" in project.sentence


async def test_a_row_with_no_committed_date_runs_open_and_says_it() -> None:
    registry, _store = await _reconstructed()

    view = await registry.timeline_service().project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", as_of=TODAY
    )
    store_row = next(row for row in view.rows if row.row_id == "pod:pod-store")

    assert store_row.committed is None and store_row.committed_history == ()
    assert "no committed date" in store_row.sentence


async def test_a_bug_that_is_no_requirement_is_marked_on_the_day_it_was_opened() -> None:
    registry, store = await _reconstructed()
    await registry.delivery_service().save_stage_mapping(
        TENANT, replace(DEFAULT_STAGE_MAPPING, requirement_types=("Story",)), actor="admin"
    )
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-30",
            name="Card form rejects a valid expiry",
            metadata={  # type: ignore[arg-type]
                "key": "CHK-30",
                "status": "To Do",
                "state": "todo",
                "issue_type": "Bug",
                "created_at": "2026-10-09T10:00:00+00:00",
            },
        )
    )
    await store.add_edge(_contains("pod-pay", "CHK-30"))

    view = await registry.timeline_service().project_timeline(
        _principal(Role.MGR), "checkout", as_of=TODAY
    )
    project = _markers(view.rows[0])
    store_row = _markers(next(row for row in view.rows if row.row_id == "pod:pod-store"))

    bug = project[(FRIDAY, "bug")]
    assert [(item.key, item.stage) for item in bug.items] == [("CHK-30", None)]
    assert (FRIDAY, "bug") in _markers(next(r for r in view.rows if r.row_id == "pod:pod-pay"))
    assert (FRIDAY, "bug") not in store_row


async def test_a_release_row_is_its_own_with_the_jira_date_beside_it() -> None:
    registry, store = await _world()

    for key in ("CHK-2", "CHK-3"):
        node = await store.get_node(TENANT, key)
        assert node is not None
        await store.upsert_node(
            replace(
                node,
                metadata={
                    **node.metadata,
                    "fix_versions": "1.0",
                    "fix_version_dates": json.dumps({"1.0": "2026-11-30"}),
                },
            )
        )
    release = await registry.forecast_service().save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1.0",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="1.0"),
        actor="dev-mina",
    )
    service = registry.timeline_service()

    whole = await service.project_timeline(_principal(Role.PO, "dev-mina"), "checkout", as_of=TODAY)
    one = await service.project_timeline(
        _principal(Role.PO, "dev-mina"), "checkout", release_id=release.release_id, as_of=TODAY
    )

    assert [row.row_id for row in whole.rows][:2] == [
        "project:checkout",
        f"release:{release.release_id}",
    ]
    assert one.rows[0].row_id == f"release:{release.release_id}"
    assert one.rows[0].jira_release_date == date(2026, 11, 30)
    assert all(row.scope.kind.value != "project" for row in one.rows)


async def test_the_impact_read_carries_the_same_blocker_markers_for_the_same_readers() -> None:
    registry, store = await _reconstructed()
    await _blockers(store)
    impact = registry.delivery_impact_service()
    scope = PAY

    async def kinds(role: Role, subject: str) -> set[str]:
        principal = _principal(role, subject)
        view = await impact.impact(
            TENANT,
            scope,
            period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
            measure=Measure.P85,
            as_of=TODAY,
            access=await impact.access(principal, scope),
        )
        return {marker.kind for marker in view.markers}

    assert {"blocker_opened", "blocker_closed"} <= await kinds(Role.MGR, "someone")
    assert {"blocker_opened", "blocker_closed"} <= await kinds(Role.SM, "dev-dana")
    assert not {"blocker_opened", "blocker_closed"} & await kinds(Role.EXEC, "someone")
    assert not {"blocker_opened", "blocker_closed"} & await kinds(Role.PO, "dev-mina")


# --- The endpoints ------------------------------------------------------------------------


async def test_the_endpoints_read_as_the_service_and_refuse_in_words() -> None:
    registry, _store = await _reconstructed()
    day = f"as_of={TODAY.isoformat()}"
    with _client(registry) as client:
        po = client.get(f"/projects/checkout/timeline?{day}", headers=_as("po", "dev-mina"))
        sm = client.get(f"/projects/checkout/timeline?{day}", headers=_as("sm", "dev-dana"))
        dev = client.get(f"/projects/checkout/timeline?{day}", headers=_as("dev", "dev-asha"))
        own = client.get(f"/pods/pod-pay/timeline?{day}", headers=_as("dev", "dev-asha"))
        other = client.get(f"/pods/pod-store/timeline?{day}", headers=_as("dev", "dev-asha"))
        missing = client.get(f"/projects/nowhere/timeline?{day}", headers=_as("po"))
        no_release = client.get(
            f"/projects/checkout/timeline?{day}&release_id=gone", headers=_as("po")
        )

    assert po.status_code == 200, po.text
    body = po.json()
    assert body["axis"]["today"] == TODAY.isoformat()
    head = body["rows"][0]
    assert head["row"] == "project:checkout" and head["access"] == "panel"
    assert head["baseline"] == {"day": PLANNED.isoformat(), "reconstructed": False}
    assert head["committed"]["date"] == "2026-11-27"
    assert [tick["date"] for tick in head["committed"]["history"]] == ["2026-11-20", "2026-11-27"]
    assert head["forecast"]["on"] == FRIDAY.isoformat()
    assert head["progress"]["unit"] == "requirements"
    assert head["scope"]["now"] - head["scope"]["at_baseline"] == 2
    assert {marker["kind"] for marker in head["markers"]} >= {
        "scope_added",
        "late",
        "committed_date_changed",
    }
    assert [text["row"] for text in body["text"]] == [row["row"] for row in body["rows"]]
    assert body["text"][0]["sentence"].startswith("Checkout Revamp: ")
    assert sm.status_code == 200
    assert {row["row"]: row["access"] for row in sm.json()["rows"]} == {
        "pod:pod-pay": "panel",
        "pod:pod-store": "dates",
    }
    dated = next(row for row in sm.json()["rows"] if row["access"] == "dates")
    assert dated["progress"] is None and dated["markers"] == [] and dated["scope"] is None
    assert dev.status_code == 403 and dev.json()["detail"] == "Not allowed."
    assert own.status_code == 200 and own.json()["rows"][0]["reference_date"] == "2026-11-27"
    assert other.status_code == 403
    assert missing.status_code == 404
    assert no_release.status_code == 404


# --- The rules: order, axis and words --------------------------------------------------------


def test_rows_are_listed_worst_verdict_first_then_by_name() -> None:
    listed = sorted(
        [
            ("Zeta Pod", Verdict.ON_TRACK),
            ("Alpha Pod", Verdict.AT_RISK),
            ("Beta Pod", Verdict.OFF_TRACK),
            ("Gamma Pod", None),
            ("Delta Pod", Verdict.NO_DATE),
        ],
        key=lambda item: worst_first(item[1], item[0]),
    )

    assert [name for name, _verdict in listed] == [
        "Beta Pod",
        "Alpha Pod",
        "Delta Pod",
        "Gamma Pod",
        "Zeta Pod",
    ]


def test_the_axis_spans_the_earliest_start_to_past_the_last_date() -> None:
    drawn = axis([date(2026, 10, 1), date(2026, 9, 20)], [date(2026, 12, 2), None], TODAY)
    short = axis([TODAY], [], TODAY)
    empty = axis([], [], TODAY)

    assert drawn.start == date(2026, 9, 20) and drawn.end == date(2026, 12, 9)
    assert (short.end - short.start).days == 28
    assert empty.start < TODAY < empty.end


def test_a_row_without_a_band_says_why_from_its_kept_numbers() -> None:
    assert no_forecast_reason(None, None) == "No forecast kept yet"
    assert no_forecast_reason(4, 10) == "No forecast yet: 4 of 10 working days of history"
    assert no_forecast_reason(20, 10, completed=0, remaining=5) == (
        "No forecast: nothing reached production in the last 20 working days"
    )
    assert no_forecast_reason(20, 10, completed=3, remaining=0) == "Nothing left to finish"


def test_a_row_reads_as_one_sentence() -> None:
    whole = row_sentence(
        SentenceParts(
            name="Checkout Revamp",
            done=8,
            total=22,
            verdict=Verdict.AT_RISK,
            p85=date(2026, 12, 2),
            committed=date(2026, 11, 18),
            committed_history=(date(2026, 11, 4), date(2026, 11, 18)),
            planned=True,
            added=4,
            added_without_label=2,
        )
    )
    dates = row_sentence(
        SentenceParts(name="Storefront Pod", p85=date(2026, 11, 13), committed=date(2026, 11, 18))
    )
    waiting = row_sentence(
        SentenceParts(
            name="Identity Pod",
            done=2,
            total=8,
            forecast_reason=no_forecast_reason(4, 10),
            committed=None,
            planned=False,
        )
    )
    points = row_sentence(
        SentenceParts(name="Payments Pod", done=13, total=40, unit="story points", planned=True)
    )
    done = row_sentence(
        SentenceParts(name="Data Pod", done=5, total=5, verdict=Verdict.DONE, planned=True)
    )

    assert whole == (
        "Checkout Revamp: 8 of 22 done; the 85% date, 2 Dec, is 10 working days after the "
        "committed 18 Nov; the committed date moved once, 4 Nov → 18 Nov; 4 requirements "
        "added since planning, 2 without the CR label."
    )
    assert dates == (
        "Storefront Pod: the 85% date, 13 Nov, is 3 working days before the committed 18 Nov."
    )
    assert waiting == (
        "Identity Pod: 2 of 8 done; no forecast yet: 4 of 10 working days of history, "
        "no committed date; planning is not marked complete yet."
    )
    assert points.startswith("Payments Pod: 13 of 40 story points done;")
    assert "nothing added since planning" in points
    assert done.startswith("Data Pod: all 5 requirements done;")
