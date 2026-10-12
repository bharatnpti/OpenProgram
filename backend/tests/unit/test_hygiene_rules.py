"""Jira hygiene's rules, as pure functions: which issues a rule reads and what each breaks."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from core.domain.delivery import DeliveryStage
from core.domain.hygiene import (
    BaselineFacts,
    Condition,
    ConditionKind,
    FixValues,
    HygieneError,
    HygieneRule,
    HygieneSettings,
    IssueContext,
    IssueField,
    Owner,
    OwnerKind,
    RuleScope,
    RuleSeverity,
    Select,
    StageFilter,
    ask_text,
    count_line,
    default_examples,
    evaluate_rule,
    fix_values,
    plus_working_days,
    render_fix,
    rule_words,
    selected,
    validated_rule,
    validated_settings,
)
from core.domain.release_readiness import Matcher, MatcherKind, ScopeIssue, Strength

TENANT = "demo"
TODAY = date(2026, 10, 14)  # a Wednesday
PLANNED = date(2026, 10, 1)  # a Thursday


def _issue(key: str = "CHK-1", **fields: object) -> ScopeIssue:
    base = ScopeIssue(
        key=key,
        title="Saved cards",
        issue_type="Story",
        stage=DeliveryStage.RAISED,
        requirement=True,
    )
    return replace(base, **fields)  # type: ignore[arg-type]


def _rule(condition: Condition, **changes: object) -> HygieneRule:
    base = HygieneRule(
        tenant_id=TENANT, rule_id="r", name="Rule", scope=RuleScope.PROJECT, condition=condition
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _context(**changes: object) -> IssueContext:
    return replace(IssueContext(today=TODAY), **changes)  # type: ignore[arg-type]


def _baseline(*keys: str, label: str = "CR", grace: int = 2) -> BaselineFacts:
    return BaselineFacts(
        planned_on=PLANNED, keys=frozenset(keys), change_label=label, grace_working_days=grace
    )


CR = Condition(kind=ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL)


def test_a_rule_reads_requirements_by_default_and_every_matcher_must_match() -> None:
    story = _issue(labels=("payments",))
    bug = _issue("CHK-2", issue_type="Bug", requirement=False)
    rule = _rule(
        Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.STORY_POINTS),
        select=Select(
            matchers=(
                Matcher(kind=MatcherKind.ISSUE_TYPE, value="story", strength=Strength.EVIDENCE),
                Matcher(kind=MatcherKind.LABEL, value="payments", strength=Strength.EVIDENCE),
            )
        ),
    )

    assert selected(rule, story)
    assert not selected(rule, replace(story, labels=()))
    assert not selected(rule, bug)
    assert selected(replace(rule, select=Select(every_type=True)), bug)


@pytest.mark.parametrize(
    ("stage_filter", "stage", "expected"),
    [
        (StageFilter.BEFORE, DeliveryStage.RAISED, True),
        (StageFilter.BEFORE, DeliveryStage.IN_DEVELOPMENT, False),
        (StageFilter.AT_OR_PAST, DeliveryStage.IN_DEVELOPMENT, True),
        (StageFilter.AT_OR_PAST, DeliveryStage.GROOMED, False),
    ],
)
def test_the_stage_filter(stage_filter: StageFilter, stage: DeliveryStage, expected: bool) -> None:
    rule = _rule(
        Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.DUE_DATE),
        select=Select(stage_filter=stage_filter, stage=DeliveryStage.IN_DEVELOPMENT),
    )
    assert selected(rule, _issue(stage=stage)) is expected
    assert not selected(rule, _issue(stage=None))


def test_added_after_planning_without_the_change_label() -> None:
    rule = _rule(CR)
    later = _issue("CHK-20", created_at="2026-10-09T10:00:00+00:00")

    breach = evaluate_rule(
        rule, later, _context(baseline=_baseline("CHK-1"), joined_on=date(2026, 10, 9))
    )

    assert breach is not None
    assert breach.what == "Joined the scope on 9 Oct, after planning on 1 Oct, without the CR label"
    assert breach.detail["baseline_on"] == "2026-10-01"
    assert breach.detail["labels"] == ["CR"]
    assert breach.waits_from == date(2026, 10, 9)


def test_the_change_label_planned_work_grace_and_no_baseline_say_nothing() -> None:
    rule = _rule(CR)
    labelled = _issue("CHK-20", labels=("cr",))
    planned = _issue("CHK-1")
    in_grace = _issue("CHK-21")

    assert evaluate_rule(rule, labelled, _context(baseline=_baseline())) is None
    assert evaluate_rule(rule, planned, _context(baseline=_baseline("CHK-1"))) is None
    # Grace of 2 working days after Thu 1 Oct runs to Mon 5 Oct.
    assert plus_working_days(PLANNED, 2) == date(2026, 10, 5)
    assert (
        evaluate_rule(rule, in_grace, _context(baseline=_baseline(), joined_on=date(2026, 10, 5)))
        is None
    )
    assert evaluate_rule(rule, in_grace, _context(baseline=None)) is None


def test_a_join_day_not_known_waits_for_the_grace_to_pass() -> None:
    rule = _rule(CR)
    issue = _issue("CHK-30")

    early = evaluate_rule(rule, issue, _context(today=date(2026, 10, 2), baseline=_baseline()))
    late = evaluate_rule(rule, issue, _context(baseline=_baseline()))

    assert early is None
    assert late is not None
    assert late.what == "Added after planning on 1 Oct, without the CR label"
    assert late.waits_from == date(2026, 10, 5)


def test_the_change_labels_are_the_tenants_and_the_baselines_not_a_fixed_word() -> None:
    rule = _rule(CR)
    issue = _issue("CHK-20", labels=("agreed-change",))
    context = _context(baseline=_baseline(label="scope-change"), change_labels=("agreed-change",))
    other = _context(baseline=_baseline(label="scope-change"))

    assert evaluate_rule(rule, issue, context) is None
    breach = evaluate_rule(rule, issue, other)
    assert breach is not None and "the scope-change label" in breach.what


@pytest.mark.parametrize(
    ("field", "kept", "words"),
    [
        (IssueField.STORY_POINTS, {"story_points": 3.0}, "No story points"),
        (IssueField.DUE_DATE, {"due_date": "2026-10-20"}, "No due date"),
        (IssueField.PARENT, {"parent_key": "CHK-100"}, "No epic or parent"),
        (IssueField.FIX_VERSION, {"fix_versions": ("R1",)}, "No fix version"),
        (IssueField.PRIORITY, {"priority": "High"}, "No priority"),
        (IssueField.ASSIGNEE, {"assigned": True}, "Nobody assigned"),
        (IssueField.COMPONENTS, {"components": ("web",)}, "No component"),
        (IssueField.SEVERITY, {"severity": "S2"}, "No severity"),
    ],
)
def test_a_missing_field(field: IssueField, kept: dict[str, object], words: str) -> None:
    rule = _rule(Condition(kind=ConditionKind.MISSING_FIELD, field=field))

    breach = evaluate_rule(rule, _issue(), _context())

    assert breach is not None and breach.what == words
    assert breach.detail == {"field": field.value}
    assert evaluate_rule(rule, _issue(**kept), _context()) is None


def test_a_missing_label_and_a_too_big_issue() -> None:
    labels = _rule(Condition(kind=ConditionKind.MISSING_LABEL, labels=("team-a", "team-b")))
    big = _rule(Condition(kind=ConditionKind.TOO_BIG, points_over=8))

    missing = evaluate_rule(labels, _issue(labels=("other",)), _context())
    assert missing is not None and missing.what == "Has no label team-a or team-b"
    assert evaluate_rule(labels, _issue(labels=("Team-B",)), _context()) is None
    too_big = evaluate_rule(big, _issue(story_points=13.0), _context())
    assert too_big is not None and too_big.what == "13 story points, over 8"
    assert evaluate_rule(big, _issue(story_points=8.0), _context()) is None


def test_acceptance_criteria_are_read_from_the_gates_scan_only_once_it_ran() -> None:
    rule = _rule(Condition(kind=ConditionKind.MISSING_GATE_KIND, gate_kind="acceptance"))
    labels = {"acceptance": "Acceptance criteria"}

    unread = evaluate_rule(rule, _issue(), _context(gate_kind_labels=labels))
    missing = evaluate_rule(
        rule,
        _issue(),
        _context(gate_scanned=True, gate_kind_labels=labels, gate_asks=frozenset({"acceptance"})),
    )
    found = evaluate_rule(
        rule,
        _issue(),
        _context(gate_scanned=True, gate_kinds=frozenset({"acceptance"}), gate_kind_labels=labels),
    )

    assert unread is None
    assert missing is not None
    assert missing.what == "No acceptance criteria found in its Jira text"
    # The gate already asks for it, so the finding lists but asks nothing.
    assert missing.detail["gate_asks"] is True
    assert found is None


def test_stale_in_a_stage_counts_working_days_since_the_last_change() -> None:
    rule = _rule(
        Condition(
            kind=ConditionKind.STALE_IN_STATUS, stage=DeliveryStage.IN_DEVELOPMENT, working_days=5
        )
    )
    issue = _issue(stage=DeliveryStage.IN_DEVELOPMENT)

    stale = evaluate_rule(rule, issue, _context(last_changed_on=date(2026, 10, 5)))
    fresh = evaluate_rule(rule, issue, _context(last_changed_on=date(2026, 10, 9)))

    assert stale is not None
    assert stale.what == "No Jira change for 7 working days in In development"
    assert stale.detail["days"] == 7
    assert fresh is None
    assert (
        evaluate_rule(
            rule,
            replace(issue, stage=DeliveryStage.IN_TESTING),
            _context(last_changed_on=date(2026, 9, 1)),
        )
        is None
    )


def test_past_due_while_open() -> None:
    rule = _rule(Condition(kind=ConditionKind.PAST_DUE_OPEN, working_days=1))

    late = evaluate_rule(rule, _issue(due_date="2026-10-07"), _context())
    within = evaluate_rule(rule, _issue(due_date="2026-10-13"), _context())
    done = evaluate_rule(rule, _issue(due_date="2026-10-01", done=True), _context())

    assert late is not None and late.what == "Due 7 Oct, 5 working days ago, and still open"
    assert late.waits_from == date(2026, 10, 7)
    assert within is None and done is None


def test_validation_names_every_problem_in_one_sentence() -> None:
    bad = _rule(
        Condition(kind=ConditionKind.STALE_IN_STATUS, working_days=0),
        name=" ",
        select=Select(
            stage_filter=StageFilter.BEFORE,
            matchers=(
                Matcher(kind=MatcherKind.LABEL, value="two words", strength=Strength.EVIDENCE),
            ),
        ),
        owner=Owner(kind=OwnerKind.PROFILE, profile="Not A Slug"),
        suggested_fix="Fix {key} for {customer}",
    )

    with pytest.raises(HygieneError) as error:
        validated_rule(bad)

    message = str(error.value)
    for part in (
        "a name of 1 to 80",
        "has a space",
        "name the stage the issues sit before",
        "name the stage an issue sits in",
        "the days without a change are 1 to 60",
        "profile list's ids",
        "{customer}",
    ):
        assert part in message


def test_validation_tidies_a_good_rule() -> None:
    rule = _rule(
        Condition(kind=ConditionKind.MISSING_LABEL, labels=(" team-a ", "team-a", "team-b")),
        name="  Team   label ",
        owner=Owner(kind=OwnerKind.PROFILE, profile=" QA "),
        suggested_fix="  Label   {key}  ",
    )

    checked = validated_rule(rule)

    assert checked.name == "Team label"
    assert checked.condition.labels == ("team-a", "team-b")
    assert checked.owner.profile == "qa"
    assert checked.suggested_fix == "Label {key}"


def test_settings_keep_the_default_change_label_when_none_is_named() -> None:
    settings = validated_settings(HygieneSettings(tenant_id=TENANT, change_labels=(" ",)))
    assert settings.change_labels == ("CR",)
    with pytest.raises(HygieneError):
        validated_settings(HygieneSettings(tenant_id=TENANT, change_labels=("two words",)))


def test_the_examples_list_only_and_are_generic() -> None:
    examples = default_examples(TENANT)

    assert len(examples) == 8
    assert all(rule.severity is RuleSeverity.LIST_ONLY for rule in examples)
    assert all(validated_rule(rule) for rule in examples)
    added = next(
        rule
        for rule in examples
        if rule.condition.kind is ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL
    )
    # The change label is tenant data: the example names none of its own.
    assert added.condition.labels == ()
    assert added.owner.kind is OwnerKind.DECISION_OWNER


def test_words_for_a_rule_its_fix_its_ask_and_the_count_line() -> None:
    rule = next(
        rule for rule in default_examples(TENANT) if rule.rule_id == "story-without-estimate"
    )
    added = next(
        rule for rule in default_examples(TENANT) if rule.rule_id.startswith("added-after")
    )

    assert (
        rule_words(rule) == "Story issues before In development without story points; lists them."
    )
    assert render_fix(rule, FixValues(key="CHK-3")) == "Estimate CHK-3 before it starts."
    values = fix_values(
        added,
        {"labels": ["CR"], "baseline_on": "2026-10-01"},
        key="CHK-20",
        title="Saved cards",
        release="Release 1",
    )
    assert (
        render_fix(added, values)
        == "Label CHK-20 CR if it was agreed as a change, or take it out of Release 1."
    )
    assert ask_text(rule, ["CHK-3", "CHK-7"], "Checkout Revamp") == (
        "Story without an estimate in Checkout Revamp: CHK-3, CHK-7"
    )
    keys = [f"CHK-{n}" for n in range(1, 8)]
    assert ask_text(added, keys, "Checkout Revamp") == (
        "Checkout Revamp: label 7 additions after planning as changes or take them out: "
        "CHK-1, CHK-2, CHK-3, CHK-4, CHK-5 and 2 more"
    )
    assert count_line(7, 2) == "Jira hygiene: 7 open (2 new today)."
    assert count_line(0, 0) == "Jira hygiene: nothing open."
