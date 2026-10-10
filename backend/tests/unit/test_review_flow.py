"""The stages a pull or merge request passes, and the type of work it is.

Stage boundaries come from the request's history (commits, draft marks, notes,
approvals); the type from the first rule that names one: a dependency bot,
the linked Jira issue's type, a label, a conventional title prefix, a branch
prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.domain.integrations import PullRequestEvent, PullRequestEventKind
from core.domain.review_flow import (
    RequestType,
    ReviewStage,
    ReviewTimeline,
    TypeSource,
    classify_request,
    current_stage,
    is_bot_login,
    labels_from_payload,
    labels_payload,
    percentile,
    review_timeline,
    stage_hours,
    timeline_from_payload,
    timeline_payload,
)

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
Kind = PullRequestEventKind


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def event(kind: PullRequestEventKind, hours: float, actor: str | None = None) -> PullRequestEvent:
    return PullRequestEvent(kind=kind, at=at(hours), actor=actor)


def classify(
    title: str = "Something",
    *,
    branch: str | None = None,
    labels: tuple[str, ...] = (),
    author: str | None = "zoe",
    author_name: str | None = None,
    issue_types: dict[str, str] | None = None,
) -> tuple[RequestType, TypeSource, str | None]:
    result = classify_request(
        title=title,
        branch=branch,
        labels=labels,
        author=author,
        author_name=author_name,
        issue_types=issue_types or {},
    )
    return result.request_type, result.source, result.evidence


# ---- classification -------------------------------------------------------------------------


def test_the_linked_jira_issue_type_names_the_work() -> None:
    types = {"CHK-3": "Bug", "CHK-12": "Story"}

    assert classify("CHK-3 Payment intent API", issue_types=types)[:2] == (
        RequestType.BUG_FIX,
        TypeSource.ISSUE,
    )
    # The key may be in the branch only, and is matched whatever its case.
    assert classify("Promo codes", branch="chk-12-promo-codes", issue_types=types) == (
        RequestType.FEATURE,
        TypeSource.ISSUE,
        "Story CHK-12",
    )


def test_a_task_issue_says_nothing_so_the_next_rule_decides() -> None:
    assert classify("CHK-5 docs: sandbox setup", issue_types={"CHK-5": "Task"})[:2] == (
        RequestType.DOCUMENTATION,
        TypeSource.TITLE,
    )


def test_labels_are_read_without_their_scope_and_a_bug_outranks_a_test_label() -> None:
    assert classify(labels=("type::refactor",))[:2] == (RequestType.REFACTOR, TypeSource.LABEL)
    assert classify(labels=("Kind/Docs",))[0] is RequestType.DOCUMENTATION
    assert classify(labels=("tests", "bug"))[:3] == (RequestType.BUG_FIX, TypeSource.LABEL, "bug")
    assert classify(labels=("backend", "needs-review"))[0] is RequestType.UNCLASSIFIED


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("feat: promo codes", RequestType.FEATURE),
        ("fix(api)!: refund rounding", RequestType.BUG_FIX),
        ("chore: bump version", RequestType.CHORE),
        ("docs: sandbox setup", RequestType.DOCUMENTATION),
        ("refactor: split the cart", RequestType.REFACTOR),
        ("test: guest checkout", RequestType.TEST),
        ("perf: cache prices", RequestType.PERFORMANCE),
        ("build: pin node 22", RequestType.DEPENDENCY_UPDATE),
        ("deps: bump httpx", RequestType.DEPENDENCY_UPDATE),
        ("chore(deps): update dependency react", RequestType.DEPENDENCY_UPDATE),
        ("ci: cache uv", RequestType.CHORE),
        ("Draft: CHK-3: feat: payment intents", RequestType.FEATURE),
        ("[CHK-3] fix: declined card copy", RequestType.BUG_FIX),
        ("Fix the login loop", RequestType.UNCLASSIFIED),
        ("feature flag cleanup", RequestType.UNCLASSIFIED),
    ],
)
def test_a_conventional_title_prefix_names_the_work(title: str, expected: RequestType) -> None:
    assert classify(title)[0] is expected


def test_a_branch_prefix_names_the_work_when_nothing_else_does() -> None:
    assert classify("Promo codes", branch="feat/promo-codes")[:3] == (
        RequestType.FEATURE,
        TypeSource.BRANCH,
        "feat/",
    )
    assert classify("Totals", branch="hotfix/rounding")[0] is RequestType.BUG_FIX
    # "test-plan" is a word in a branch name, not a prefix.
    assert classify("Guest checkout", branch="test-plan-guest")[0] is RequestType.UNCLASSIFIED


def test_a_dependency_bot_wins_over_what_its_title_or_issue_says() -> None:
    assert classify("fix(deps): update dependency httpx", author="renovate[bot]") == (
        RequestType.DEPENDENCY_UPDATE,
        TypeSource.AUTHOR,
        "renovate[bot]",
    )
    assert classify("Bump x", author=None, author_name="Dependabot")[0] is (
        RequestType.DEPENDENCY_UPDATE
    )
    assert classify("Update react", branch="renovate/react-19")[1] is TypeSource.AUTHOR


def test_the_jira_type_comes_before_a_label_and_a_label_before_the_title() -> None:
    assert classify("CHK-3 feat: x", labels=("docs",), issue_types={"CHK-3": "Bug"})[0] is (
        RequestType.BUG_FIX
    )
    assert classify("feat: x", labels=("docs",))[0] is RequestType.DOCUMENTATION


def test_bot_logins() -> None:
    assert is_bot_login("renovate[bot]")
    assert is_bot_login("gitlab-bot")
    assert is_bot_login("project_7_bot_3f2a")
    assert not is_bot_login("abbott")
    assert not is_bot_login("noah.weber")


# ---- stage boundaries -----------------------------------------------------------------------


def test_a_request_opened_ready_is_ready_when_opened_and_review_needs_someone_else() -> None:
    timeline = review_timeline(
        [
            event(Kind.COMMIT, 0),
            event(Kind.COMMENT, 3, "zoe"),  # the author on her own request: not review
            event(Kind.COMMENT, 4, "gitlab-bot"),  # a bot: not review
            event(Kind.COMMENT, 18, "noah"),
            event(Kind.COMMENT, 19, "noah"),
            event(Kind.APPROVAL, 20, "noah"),
        ],
        author="zoe",
        opened_at=at(2),
        draft=False,
        merged_at=at(21),
    )

    assert timeline == ReviewTimeline(
        first_commit_at=at(0),
        ready_at=at(2),
        first_review_at=at(18),
        approved_at=at(20),
        last_review_at=at(20),
        reviewer_count=1,
    )
    assert stage_hours(timeline, merged_at=at(21)) == {
        ReviewStage.CODING: 2.0,
        ReviewStage.AWAITING_REVIEW: 16.0,
        ReviewStage.IN_REVIEW: 2.0,
        ReviewStage.AWAITING_MERGE: 1.0,
    }


def test_a_draft_is_ready_at_its_last_ready_mark_before_the_first_review() -> None:
    timeline = review_timeline(
        [
            event(Kind.COMMIT, 0),
            event(Kind.READY, 5, "zoe"),
            event(Kind.DRAFT, 6, "zoe"),
            event(Kind.READY, 8, "zoe"),
            event(Kind.COMMENT, 10, "noah"),
            # Back to draft and ready again after the review: the wait was over.
            event(Kind.DRAFT, 11, "zoe"),
            event(Kind.READY, 12, "zoe"),
            event(Kind.APPROVAL, 14, "noah"),
        ],
        author="zoe",
        opened_at=at(1),
        draft=False,
        merged_at=at(15),
    )

    assert (timeline.ready_at, timeline.first_review_at) == (at(8), at(10))
    assert stage_hours(timeline, merged_at=at(15))[ReviewStage.CODING] == 8.0


def test_a_review_on_a_draft_before_it_was_ready_does_not_end_the_wait() -> None:
    timeline = review_timeline(
        [
            event(Kind.COMMENT, 1, "noah"),
            event(Kind.READY, 4, "zoe"),
            event(Kind.COMMENT, 9, "liam"),
        ],
        author="zoe",
        opened_at=at(0),
        draft=False,
        merged_at=None,
    )

    assert (timeline.ready_at, timeline.first_review_at, timeline.reviewer_count) == (
        at(4),
        at(9),
        1,
    )


def test_an_approval_taken_back_no_longer_ends_the_review() -> None:
    timeline = review_timeline(
        [
            event(Kind.APPROVAL, 3, "noah"),
            event(Kind.APPROVAL, 4, "liam"),
            event(Kind.UNAPPROVAL, 5, "liam"),
            event(Kind.COMMENT, 6, "liam"),
        ],
        author="zoe",
        opened_at=at(0),
        draft=False,
        merged_at=at(8),
    )

    assert (timeline.approved_at, timeline.last_review_at) == (at(3), at(6))
    # In review ends at the last approval still standing; then it waits to merge.
    assert stage_hours(timeline, merged_at=at(8))[ReviewStage.AWAITING_MERGE] == 5.0


def test_activity_after_the_merge_moves_no_stage() -> None:
    timeline = review_timeline(
        [event(Kind.COMMENT, 30, "noah")],
        author="zoe",
        opened_at=at(0),
        draft=False,
        merged_at=at(2),
    )

    assert timeline.first_review_at is None
    # Merged with no review: coding only, no wait counted.
    assert stage_hours(timeline, merged_at=at(2)) == {
        ReviewStage.CODING: None,
        ReviewStage.AWAITING_REVIEW: None,
        ReviewStage.IN_REVIEW: None,
        ReviewStage.AWAITING_MERGE: None,
    }


def test_coding_is_zero_when_the_request_was_opened_before_its_first_commit() -> None:
    timeline = review_timeline(
        [event(Kind.COMMIT, 3)], author="zoe", opened_at=at(1), draft=False, merged_at=None
    )

    assert stage_hours(timeline, merged_at=None)[ReviewStage.CODING] == 0.0


def test_the_stage_an_open_request_is_in_now() -> None:
    def stage(**changes: datetime | None) -> tuple[ReviewStage, datetime | None]:
        base = ReviewTimeline(
            first_commit_at=at(0),
            ready_at=at(1),
            first_review_at=None,
            approved_at=None,
            last_review_at=None,
            reviewer_count=0,
        )
        values = {**base.__dict__, **changes}
        return current_stage(ReviewTimeline(**values), draft=False, opened_at=at(1))

    assert stage() == (ReviewStage.AWAITING_REVIEW, at(1))
    assert stage(first_review_at=at(2)) == (ReviewStage.IN_REVIEW, at(2))
    assert stage(first_review_at=at(2), approved_at=at(3)) == (ReviewStage.AWAITING_MERGE, at(3))
    assert stage(ready_at=None) == (ReviewStage.CODING, at(0))
    draft = review_timeline([], author="zoe", opened_at=at(5), draft=True, merged_at=None)
    assert current_stage(draft, draft=True, opened_at=at(5)) == (ReviewStage.CODING, at(5))


def test_the_timeline_survives_the_fact_payload() -> None:
    timeline = ReviewTimeline(
        first_commit_at=at(0),
        ready_at=at(1),
        first_review_at=at(2),
        approved_at=None,
        last_review_at=at(3),
        reviewer_count=2,
    )

    assert timeline_from_payload(timeline_payload(timeline)) == timeline
    # A fact written before histories were read has none.
    assert timeline_from_payload({"merged": True}) is None
    assert labels_from_payload({"labels": labels_payload(["bug, urgent", "ui"])}) == (
        "bug, urgent",
        "ui",
    )
    assert labels_payload([]) is None


def test_percentiles_interpolate() -> None:
    assert percentile([], 0.5) is None
    assert percentile([4.0], 0.75) == 4.0
    assert percentile([1.0, 2.0, 3.0, 10.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 10.0], 0.75) == 4.75
