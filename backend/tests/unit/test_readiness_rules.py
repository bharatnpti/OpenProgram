"""Release readiness rules: how evidence is found, how urgent a gap is, and what a draft says."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.domain.delivery import DeliveryStage
from core.domain.release_readiness import (
    AppliesTo,
    DecidedBy,
    DecisionKind,
    Draft,
    DraftContext,
    DraftTemplate,
    FindingState,
    Matcher,
    MatcherKind,
    PersonDecision,
    ReadinessError,
    ReadinessSettings,
    ReleaseCriterion,
    ScopeIssue,
    ScopeKind,
    ScopeRef,
    Severity,
    Strength,
    Urgency,
    UrgencyKind,
    applies_to_scope,
    created_footer,
    criterion_slug,
    default_examples,
    evaluate_criterion,
    finding_fingerprint,
    fold,
    is_ready,
    marker_label,
    minus_working_days,
    render_draft,
    urgency_of,
    validated_criterion,
    validated_draft,
    validated_reason,
    validated_record,
    with_article,
    working_days_left,
)

TENANT = "demo"
AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
EVIDENCE, CANDIDATE = Strength.EVIDENCE, Strength.CANDIDATE


def _criterion(*matchers: Matcher, **changes: object) -> ReleaseCriterion:
    base = ReleaseCriterion(
        tenant_id=TENANT,
        criterion_id="security-review",
        name="Security review",
        evidence="A security review of the release's changes.",
        applies_to=AppliesTo.RELEASE,
        matchers=matchers
        or (
            Matcher(kind=MatcherKind.LABEL, value="security-review", strength=EVIDENCE),
            Matcher(kind=MatcherKind.TITLE_PHRASE, value="security review", strength=EVIDENCE),
            Matcher(kind=MatcherKind.TITLE_WORDS, value="pen test", strength=CANDIDATE),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _issue(key: str, title: str, **changes: object) -> ScopeIssue:
    return replace(ScopeIssue(key=key, title=title), **changes)  # type: ignore[arg-type]


def _judge(criterion: ReleaseCriterion, *issues: ScopeIssue, **kwargs: object) -> object:
    return evaluate_criterion(criterion, issues, scope_name="Release 1", **kwargs)  # type: ignore[arg-type]


# --- Finding evidence --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("matcher", "issue", "how"),
    [
        (
            Matcher(kind=MatcherKind.LABEL, value="Security-Review", strength=EVIDENCE),
            _issue("CHK-1", "Anything", labels=("security-review",)),
            "label Security-Review",
        ),
        (
            Matcher(kind=MatcherKind.ISSUE_TYPE, value="security review", strength=EVIDENCE),
            _issue("CHK-1", "Anything", issue_type="Security Review"),
            "type Security Review",
        ),
        (
            Matcher(
                kind=MatcherKind.TITLE_PHRASE, value="data protection impact", strength=EVIDENCE
            ),
            _issue("CHK-1", "Data-protection  impact assessment"),
            'says "data protection impact"',
        ),
        (
            Matcher(kind=MatcherKind.EPIC, value="CHK-7", strength=EVIDENCE),
            _issue("CHK-1", "Anything", parent_key="CHK-7"),
            "epic CHK-7",
        ),
        (
            Matcher(kind=MatcherKind.EPIC, value="hardening", strength=EVIDENCE),
            _issue("CHK-1", "Anything", parent_key="CHK-7", parent_title="Release hardening"),
            "epic CHK-7",
        ),
    ],
)
def test_each_evidence_matcher_covers_the_criterion(
    matcher: Matcher, issue: ScopeIssue, how: str
) -> None:
    evaluation = _judge(_criterion(matcher), issue)

    assert evaluation.state is FindingState.COVERED  # type: ignore[attr-defined]
    assert [item.how for item in evaluation.evidence] == [how]  # type: ignore[attr-defined]


def test_a_phrase_must_appear_whole_but_words_may_come_in_any_order() -> None:
    phrase = Matcher(kind=MatcherKind.TITLE_PHRASE, value="load test", strength=EVIDENCE)
    words = Matcher(kind=MatcherKind.TITLE_WORDS, value="load test", strength=CANDIDATE)
    issue = _issue("CHK-13", "Test the checkout under load")

    assert _judge(_criterion(phrase), issue).state is FindingState.MISSING  # type: ignore[attr-defined]
    unsure = _judge(_criterion(phrase, words), issue)
    assert unsure.state is FindingState.UNSURE  # type: ignore[attr-defined]
    assert unsure.reason == "Only the title's words match: CHK-13."  # type: ignore[attr-defined]
    # A phrase is whole words: "payload testing" says neither "load" nor "test".
    assert _judge(_criterion(phrase), _issue("CHK-2", "Payload testing")).state is (  # type: ignore[attr-defined]
        FindingState.MISSING
    )


def test_evidence_lists_every_match_and_is_done_when_one_is() -> None:
    evaluation = _judge(
        _criterion(),
        _issue("CHK-9", "Security review, part 1", status="In Progress"),
        _issue("CHK-10", "Release 1 security review", status="Done", done=True),
        _issue("CHK-11", "Unrelated"),
    )

    assert [item.issue_key for item in evaluation.evidence] == ["CHK-9", "CHK-10"]  # type: ignore[attr-defined]
    assert evaluation.done is True  # type: ignore[attr-defined]
    assert evaluation.decided_by is DecidedBy.RULES  # type: ignore[attr-defined]


def test_an_issue_in_a_stage_that_is_not_counted_is_never_evidence() -> None:
    evaluation = _judge(_criterion(), _issue("CHK-9", "Security review", counted=False))

    assert evaluation.state is FindingState.MISSING  # type: ignore[attr-defined]
    assert evaluation.reason == (  # type: ignore[attr-defined]
        'No issue in Release 1 is labelled security-review or says "security review".'
    )


def test_needs_done_decides_whether_covered_is_ready() -> None:
    criterion = _criterion()
    open_review = _judge(criterion, _issue("CHK-9", "Security review"))

    assert not is_ready(criterion, open_review, None)  # type: ignore[arg-type]
    assert is_ready(replace(criterion, needs_done=False), open_review, None)  # type: ignore[arg-type]


def test_a_person_link_wins_and_a_vanished_key_reads_unsure() -> None:
    criterion = _criterion()
    linked = PersonDecision(kind=DecisionKind.LINKED, by="U1003", at=AT, issue_key="CHK-9")
    known = {"CHK-9": _issue("CHK-9", "Capture the review notes", status="Done", done=True)}

    covered = _judge(criterion, person=linked, known=known)
    gone = _judge(criterion, person=linked, known={})
    closed = _judge(
        criterion,
        person=linked,
        known={"CHK-9": _issue("CHK-9", "x", status="Won't Do", counted=False)},
    )
    record = _judge(
        criterion,
        person=replace(linked, issue_key="", url="https://records.example/r/1"),
    )

    assert (covered.state, covered.done, covered.decided_by) == (  # type: ignore[attr-defined]
        FindingState.COVERED,
        True,
        DecidedBy.PERSON,
    )
    assert gone.state is FindingState.UNSURE  # type: ignore[attr-defined]
    assert gone.reason == "CHK-9 is no longer in Jira's synced issues."  # type: ignore[attr-defined]
    assert closed.state is FindingState.MISSING  # type: ignore[attr-defined]
    assert closed.reason == "CHK-9 was closed as Won't Do."  # type: ignore[attr-defined]
    assert record.evidence[0].url == "https://records.example/r/1"  # type: ignore[attr-defined]


def test_not_applicable_keeps_the_rules_state_and_is_ready() -> None:
    criterion = _criterion()
    waived = PersonDecision(kind=DecisionKind.NOT_APPLICABLE, by="U1001", at=AT, reason="No UI")
    evaluation = _judge(criterion, person=waived)

    assert evaluation.state is FindingState.MISSING  # type: ignore[attr-defined]
    assert evaluation.decided_by is DecidedBy.PERSON  # type: ignore[attr-defined]
    assert is_ready(criterion, evaluation, waived)  # type: ignore[arg-type]


def test_an_only_where_condition_needs_a_counted_issue_with_the_label_or_type() -> None:
    criterion = _criterion(when_labels=("personal-data",), when_types=("Data Store",))

    assert not applies_to_scope(criterion, [_issue("A-1", "x")])
    assert applies_to_scope(criterion, [_issue("A-1", "x", labels=("Personal-Data",))])
    assert applies_to_scope(criterion, [_issue("A-1", "x", issue_type="data store")])
    assert not applies_to_scope(
        criterion, [_issue("A-1", "x", labels=("personal-data",), counted=False)]
    )
    assert applies_to_scope(_criterion(), [])


# --- Urgency -----------------------------------------------------------------------------


MONDAY = date(2026, 10, 5)


def _urgency(
    target: date | None, *stages: tuple[str, DeliveryStage], ready: bool = False
) -> Urgency:
    return urgency_of(_criterion(), ready=ready, target=target, stages=stages, today=MONDAY)


def test_working_days_are_counted_back_from_the_delivery_date() -> None:
    assert minus_working_days(date(2026, 11, 4), 10) == date(2026, 10, 21)
    assert working_days_left(MONDAY, date(2026, 10, 21)) == 12
    assert working_days_left(MONDAY, MONDAY) == 0
    assert working_days_left(date(2026, 10, 12), date(2026, 10, 9)) == -1


@pytest.mark.parametrize(
    ("target", "stages", "kind"),
    [
        (date(2026, 12, 31), (), UrgencyKind.LATER),
        (date(2026, 10, 30), (), UrgencyKind.DUE_SOON),
        (date(2026, 10, 9), (), UrgencyKind.OVERDUE),
        (None, (), UrgencyKind.NO_DATE),
        (None, (("CHK-1", DeliveryStage.BUSINESS_TESTING),), UrgencyKind.DUE_SOON),
        (date(2026, 12, 31), (("CHK-1", DeliveryStage.BUSINESS_TESTING),), UrgencyKind.DUE_SOON),
        (None, (("CHK-2", DeliveryStage.PRODUCTION),), UrgencyKind.STAGE_REACHED),
        (date(2026, 12, 31), (("CHK-2", DeliveryStage.PRODUCTION),), UrgencyKind.STAGE_REACHED),
    ],
)
def test_every_urgency(
    target: date | None, stages: tuple[tuple[str, DeliveryStage], ...], kind: UrgencyKind
) -> None:
    assert _urgency(target, *stages).kind is kind


def test_a_ready_criterion_is_never_urgent_and_the_stage_names_its_requirement() -> None:
    reached = _urgency(
        None, ("CHK-12", DeliveryStage.PRODUCTION), ("CHK-3", DeliveryStage.PRODUCTION)
    )

    assert reached.stage_key == "CHK-3"
    assert _urgency(date(2026, 10, 9), ("CHK-3", DeliveryStage.PRODUCTION), ready=True).kind is (
        UrgencyKind.LATER
    )


# --- Fingerprint, drafts and words -------------------------------------------------------


def test_the_fingerprint_changes_with_the_state_and_the_issues_but_not_by_itself() -> None:
    criterion = _criterion()
    scope = ScopeRef(kind=ScopeKind.RELEASE, id="rel-1")
    evaluation = _judge(criterion, _issue("CHK-9", "Security review"))
    urgency = _urgency(date(2026, 12, 31))

    def print_of(**changes: object) -> str:
        values: dict[str, object] = {
            "criterion_version": 1,
            "scope": scope,
            "evaluation": evaluation,
            "urgency": urgency,
            "person": None,
            "applies": True,
            "held": False,
            "updated": {"CHK-9": "2026-10-01T09:00:00+00:00"},
        }
        values.update(changes)
        return finding_fingerprint(**values)  # type: ignore[arg-type]

    assert print_of() == print_of()
    assert print_of() != print_of(criterion_version=2)
    assert print_of() != print_of(updated={"CHK-9": "2026-10-02T09:00:00+00:00"})
    assert print_of() != print_of(held=True)


def test_a_draft_is_rendered_from_names_and_dates_only() -> None:
    settings = ReadinessSettings(tenant_id=TENANT)
    context = DraftContext(
        scope_kind=ScopeKind.RELEASE,
        scope_name="Release 1",
        project_name="Checkout Revamp",
        project_key="CHK",
        release_name="Release 1",
        due_on=date(2026, 10, 21),
        delivery_date=date(2026, 11, 4),
    )
    draft = render_draft(
        _criterion(draft=DraftTemplate(labels=("security-review",))), settings, context
    )

    assert draft.summary == "Security review for Release 1 of Checkout Revamp"
    assert draft.project_key == "CHK"
    assert draft.issue_type == "Task"
    assert draft.labels == ("security-review", "release-readiness")
    assert draft.description.splitlines()[0] == (
        "Release 1 of Checkout Revamp needs a security review before production. "
        "No Jira issue in Release 1 tracks one yet."
    )
    assert "Needed by 21 Oct, 10 working days before the delivery date of 4 Nov." in (
        draft.description
    )


def test_a_template_fills_only_known_words_and_stays_short_enough() -> None:
    with pytest.raises(ReadinessError, match=r"uses \{assignee\}"):
        validated_criterion(_criterion(draft=DraftTemplate(summary="{assignee}: {criterion}")))
    with pytest.raises(ReadinessError, match="past 255 characters"):
        validated_criterion(_criterion(draft=DraftTemplate(summary=" ".join(["{scope}"] * 4))))


def test_a_criterion_is_tidied_or_refused_in_one_sentence() -> None:
    tidy = validated_criterion(_criterion(name="  Security   review "))
    assert tidy.name == "Security review"

    with pytest.raises(ReadinessError) as refused:
        validated_criterion(
            _criterion(
                Matcher(kind=MatcherKind.LABEL, value="two words", strength=EVIDENCE),
                name="",
                lead_working_days=61,
            )
        )
    message = str(refused.value)
    assert message.startswith("A criterion needs a name")
    assert "the lead is 0 to 60 working days" in message
    assert "has a space" in message
    assert message.endswith(".")


def test_a_person_edit_of_a_draft_is_checked() -> None:
    draft = Draft(
        project_key="chk",
        issue_type="Task",
        summary=" Security  review ",
        description="",
        labels=(),
    )
    assert validated_draft(draft).project_key == "CHK"
    with pytest.raises(ReadinessError, match="no spaces"):
        validated_draft(replace(draft, labels=("two words",)))
    with pytest.raises(ReadinessError, match="capital letters"):
        validated_draft(replace(draft, project_key="1X"))


def test_reasons_and_records_are_checked() -> None:
    assert validated_reason("  Internal  API only ") == "Internal API only"
    with pytest.raises(ReadinessError):
        validated_reason("no")
    assert validated_record("https://records.example/1", "  ok ") == (
        "https://records.example/1",
        "ok",
    )
    with pytest.raises(ReadinessError):
        validated_record("http://records.example/1", "")


def test_words_marker_and_slugs() -> None:
    assert with_article("Security review") == "a security review"
    assert with_article("Accessibility check") == "an accessibility check"
    assert with_article("DPIA") == "a DPIA"
    assert fold("Data-protection  Impact_assessment!") == "data protection impact assessment"
    assert marker_label("rs_1").startswith("op-rr-") and len(marker_label("rs_1")) == 14
    assert criterion_slug("Load test", ["load-test"]) == "load-test-2"
    assert created_footer("Mina Patel", "") == (
        "Drafted by OpenProgram's release readiness check. "
        "Approved and created in OpenProgram by Mina Patel."
    )


def test_the_six_examples_are_generic_valid_and_none_is_tenant_specific() -> None:
    examples = default_examples(TENANT)

    assert [item.name for item in examples] == [
        "Security review",
        "Load test",
        "Runbook and handover",
        "Data-protection impact assessment",
        "Accessibility check",
        "Change approval",
    ]
    for example in examples:
        assert validated_criterion(example) == example
    assert [item.severity for item in examples].count(Severity.ADVISORY) == 1
