"""The day's digest: what moved on a person's tasks, from the tracker's history and Git."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.domain.delivery import DeliveryStage
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.observed_day import (
    EventKind,
    digest,
    lines,
    observe_task,
    window_start,
)

UNTIL = datetime(2026, 10, 9, 17, 0, tzinfo=UTC)
SINCE = UNTIL - timedelta(days=1)


def _change(
    key: str,
    field: str,
    before: str | None,
    after: str | None,
    *,
    at: datetime,
    author: str | None = "dev-kai",
    **extra: JsonScalar,
) -> FactEvent:
    payload: dict[str, JsonScalar] = {"key": key, "field": field, "from": before, "to": after}
    if author is not None:
        payload["author_member_id"] = author
    payload.update(extra)
    return FactEvent(
        tenant_id="demo",
        source="issue_change",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id=key),
        payload=payload,
        observed_at=at,
        correlation_id=f"issue_change:demo:{key}:{at.isoformat()}:{field}",
    )


def _issue(key: str, state: str, status: str, *, at: datetime) -> FactEvent:
    return FactEvent(
        tenant_id="demo",
        source="issue",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id=key),
        payload={"key": key, "state": state, "status": status, "title": "made-up title"},
        observed_at=at,
        correlation_id=f"issue:demo:{key}:{at.isoformat()}",
    )


def _merge_request(number: str, branch: str, **times: str) -> FactEvent:
    payload: dict[str, JsonScalar] = {
        "repo": "demo/storefront",
        "id": number,
        "title": "made-up",
        "source_branch": branch,
        "web_url": f"https://git.example.test/demo/storefront/-/merge_requests/{number}",
        **times,
    }
    return FactEvent(
        tenant_id="demo",
        source="vcs_pull_request",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-kai"),
        payload=payload,
        observed_at=UNTIL - timedelta(hours=1),
        correlation_id=f"vcs:pr:{number}",
    )


def test_moves_resolutions_and_dates_read_per_task() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-4", "status", "In Progress", "In Review", at=UNTIL - timedelta(hours=5)),
            _change("CHK-7", "resolution", None, "Done", at=UNTIL - timedelta(hours=3)),
            _change("IDP-3", "due_date", "2026-10-10", "2026-10-14", at=UNTIL - timedelta(hours=2)),
            # Before the window: not listed.
            _change("CHK-4", "status", "To Do", "In Progress", at=SINCE - timedelta(days=2)),
        ],
        vcs_facts=[_merge_request("12", "CHK-4-intent", opened_at=UNTIL.isoformat())],
        assigned_keys={"CHK-4", "CHK-7", "IDP-3"},
    )

    assert day.has_events
    assert day.from_tracker and day.from_vcs
    assert lines(day) == (
        "CHK-4 moved to In Review (in development) and merge request !12 opened",
        "IDP-3 due date moved Oct 10 → Oct 14",
        "CHK-7 resolved",
    )


def test_a_move_across_stages_names_both_and_a_move_back_says_so() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-4", "status", "In Review", "QA", at=UNTIL - timedelta(hours=4)),
            _change("CHK-5", "status", "Done", "In Progress", at=UNTIL - timedelta(hours=3)),
        ],
        assigned_keys={"CHK-4", "CHK-5"},
    )

    assert lines(day) == (
        "CHK-5 moved back to In Progress (production → in development)",
        "CHK-4 moved to QA (in development → in testing)",
    )
    move = day.tasks[1].events[0]
    assert move.kind is EventKind.MOVED
    assert move.to_stage is DeliveryStage.IN_TESTING


def test_only_issue_moved_to_done_is_evidence() -> None:
    """Done work counts: a person whose only issue moved to Done is not "no active work"."""
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-9", "status", "In Review", "Done", at=UNTIL - timedelta(hours=1)),
            _change("CHK-9", "resolution", None, "Done", at=UNTIL - timedelta(hours=1)),
        ],
        assigned_keys={"CHK-9"},
    )

    assert day.has_events
    assert lines(day) == ("CHK-9 moved to Done (in development → production)",)


def test_several_moves_read_as_one_and_a_round_trip_is_none() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-4", "status", "In Progress", "In Review", at=UNTIL - timedelta(hours=6)),
            _change("CHK-4", "status", "In Review", "QA", at=UNTIL - timedelta(hours=2)),
            _change("CHK-6", "status", "In Progress", "Blocked", at=UNTIL - timedelta(hours=6)),
            _change("CHK-6", "status", "Blocked", "In Progress", at=UNTIL - timedelta(hours=2)),
        ],
        assigned_keys={"CHK-4", "CHK-6"},
    )

    assert lines(day) == ("CHK-4 moved to QA (in development → in testing)",)


def test_others_issues_the_person_changed_are_worked_on() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("IDP-2", "story_points", None, "3", at=UNTIL - timedelta(hours=2)),
            # Someone else's change on someone else's issue: nothing to do with Kai.
            _change("IDP-5", "status", "To Do", "Done", at=UNTIL, author="dev-ira"),
        ],
        assigned_keys=set(),
    )

    assert day.has_events
    assert day.worked_on == ("IDP-2",)
    assert lines(day) == ("worked on IDP-2",)


def test_assignment_in_the_window_makes_the_task_theirs() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change(
                "CHK-8",
                "assignee",
                "acct-ira",
                "acct-kai",
                at=UNTIL - timedelta(hours=2),
                author="dev-ira",
                to_member_id="dev-kai",
                from_member_id="dev-ira",
            ),
            _change(
                "CHK-2",
                "assignee",
                "acct-kai",
                "acct-ira",
                at=UNTIL - timedelta(hours=1),
                author="dev-ira",
                from_member_id="dev-kai",
                to_member_id="dev-ira",
            ),
        ],
        assigned_keys=set(),
    )

    assert lines(day) == ("CHK-2 assigned away from them", "CHK-8 assigned to them")


def test_a_quiet_task_under_way_says_how_long_and_is_not_evidence() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-11", "status", "To Do", "In Progress", at=UNTIL - timedelta(days=8)),
            # A label change is not movement.
            _change("CHK-11", "labels", None, "x", at=UNTIL - timedelta(hours=1)),
        ],
        issue_facts=[_issue("CHK-11", "in_progress", "In Progress", at=UNTIL - timedelta(days=8))],
        assigned_keys={"CHK-11"},
    )

    assert not day.has_events
    assert lines(day) == ("no movement on CHK-11 for 8 days",)


def test_a_quiet_task_falls_back_to_when_its_status_began() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[],
        issue_facts=[
            _issue("CHK-12", "todo", "To Do", at=UNTIL - timedelta(days=12)),
            _issue("CHK-12", "in_progress", "In Progress", at=UNTIL - timedelta(days=6)),
            _issue("CHK-12", "in_progress", "In Progress", at=UNTIL - timedelta(days=2)),
        ],
        assigned_keys={"CHK-12"},
    )

    assert [task.quiet_days for task in day.quiet] == [6]


def test_a_task_the_person_updated_is_not_quiet() -> None:
    update = FactEvent(
        tenant_id="demo",
        source="task_update",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="CHK-11"),
        payload={"issue_key": "CHK-11", "developer_id": "dev-kai", "state": "in_progress"},
        observed_at=UNTIL - timedelta(hours=2),
        correlation_id="update",
    )
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-11", "status", "To Do", "In Progress", at=UNTIL - timedelta(days=8))
        ],
        issue_facts=[_issue("CHK-11", "in_progress", "In Progress", at=UNTIL - timedelta(days=8))],
        task_updates=[update],
        assigned_keys={"CHK-11"},
    )

    assert day.quiet == ()


def test_commits_count_per_task_and_merge_requests_by_number_only() -> None:
    commits = [
        FactEvent(
            tenant_id="demo",
            source="vcs_commit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-kai"),
            payload={"repo": "demo/storefront", "sha": f"abc{index}", "message": message},
            observed_at=UNTIL - timedelta(hours=index + 1),
            correlation_id=f"commit:{index}",
        )
        for index, message in enumerate(
            ("chk-4: wire the intent", "CHK-4 tests", "tidy up, nothing named")
        )
    ]
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[],
        vcs_facts=[
            *commits,
            _merge_request("5", "spike-branch", merged_at=(UNTIL - timedelta(hours=1)).isoformat()),
        ],
        assigned_keys={"CHK-4"},
    )

    assert lines(day) == ("CHK-4 named in 2 commits", "merge request !5 merged")
    assert all("wire" not in line and "tidy" not in line for line in lines(day))


def test_values_are_never_free_text() -> None:
    day = digest(
        "dev-kai",
        SINCE,
        UNTIL,
        issue_changes=[
            _change("CHK-4", "summary", None, None, at=UNTIL - timedelta(hours=1)),
            _change("CHK-4", "description", None, None, at=UNTIL - timedelta(hours=1)),
        ],
        assigned_keys={"CHK-4"},
    )

    # A text edit on one's own task says nothing the digest names.
    assert not day.has_events


def test_observe_task_says_the_status_since_when_and_who_moved_it() -> None:
    observed = observe_task(
        "CHK-4",
        [
            _issue("CHK-4", "in_progress", "In Review", at=UNTIL - timedelta(hours=3)),
            _change(
                "CHK-4",
                "status",
                "In Progress",
                "In Review",
                at=UNTIL - timedelta(hours=3),
                author="dev-liam",
            ),
            _change("CHK-4", "due_date", "2026-10-10", "2026-10-14", at=UNTIL - timedelta(hours=2)),
            _change(
                "CHK-4",
                "assignee",
                "acct-kai",
                "acct-ira",
                at=UNTIL - timedelta(hours=1),
                to_member_id="dev-ira",
            ),
        ],
        start=UNTIL.replace(hour=0),
        until=UNTIL,
        names={"dev-ira": "Ira"},
    )

    assert observed.status == "In Review"
    assert observed.stage is DeliveryStage.IN_DEVELOPMENT
    assert observed.since == UNTIL - timedelta(hours=3)
    assert observed.moved_by_member_id == "dev-liam"
    assert observed.changes == ("due date moved Oct 10 → Oct 14", "assigned to Ira")
    assert not observed.is_empty


def test_observe_task_without_history_is_empty_and_a_quiet_one_says_so() -> None:
    plain = observe_task(
        "CHK-4",
        [_issue("CHK-4", "in_progress", "In Progress", at=UNTIL - timedelta(hours=3))],
        start=UNTIL.replace(hour=0),
        until=UNTIL,
    )
    quiet = observe_task(
        "CHK-9",
        [
            _issue("CHK-9", "in_progress", "In Progress", at=UNTIL - timedelta(days=9)),
            _change("CHK-9", "status", "To Do", "In Progress", at=UNTIL - timedelta(days=9)),
        ],
        start=UNTIL.replace(hour=0),
        until=UNTIL,
    )

    assert plain.is_empty
    assert quiet.quiet_days == 9
    assert quiet.since == UNTIL - timedelta(days=9)


def test_window_starts_at_the_later_of_the_last_answer_and_the_previous_checkin() -> None:
    previous = UNTIL - timedelta(days=1)
    answered = UNTIL - timedelta(hours=3)

    assert window_start(previous, answered, until=UNTIL) == answered
    assert window_start(previous, None, until=UNTIL) == previous
    assert window_start(None, None, until=UNTIL) == UNTIL - timedelta(days=1)
