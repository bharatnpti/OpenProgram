"""N8: a check-in's signals reflect every message of it, the newest one winning."""

from __future__ import annotations

from core.application.checkin_signals import merge_checkin_signals
from core.domain.blockers import BlockerReport
from core.domain.status import CheckInSignals, CrossPersonMention, IssueClaim

# R2, Raj Iyer: his first reply, then his answer to "What is your ETA to start or
# complete INS-4?", each as the model read it on its own.
_RAJ_FIRST = CheckInSignals(
    progress_note=(
        "INS-2 backfill is done code-wise, only waiting on the merge of insights-pipeline !1; "
        "INS-3 is merged (!2); INS-4 not started"
    ),
    blockers_answered=True,
    issue_updates=(
        IssueClaim(
            issue_key="INS-2",
            claimed_done=True,
            claimed_state="done",
            note="Backfill done code-wise; waiting on the merge of insights-pipeline !1.",
        ),
        IssueClaim(
            issue_key="INS-3",
            claimed_done=True,
            claimed_state="merged",
            note="Merged in insights-pipeline !2.",
        ),
        IssueClaim(issue_key="INS-4", claimed_state="not started", note="Not started yet."),
    ),
)
_RAJ_ANSWER = CheckInSignals(
    progress_note="INS-4 starts Monday once !1 is merged; wrapped up by midweek.",
    eta_answered=True,
    issue_updates=(
        IssueClaim(
            issue_key="INS-4",
            claimed_state="starting Monday",
            note="Starts Monday once !1 is merged, done by midweek.",
        ),
    ),
)


def _claims(signals: CheckInSignals) -> dict[str, IssueClaim]:
    return {claim.issue_key: claim for claim in signals.issue_updates}


def test_a_first_message_is_kept_exactly() -> None:
    assert merge_checkin_signals(None, _RAJ_FIRST) is _RAJ_FIRST


def test_raj_answer_to_the_follow_up_keeps_what_his_first_reply_said() -> None:
    merged = merge_checkin_signals(_RAJ_FIRST, _RAJ_ANSWER)

    claims = _claims(merged)
    assert list(claims) == ["INS-2", "INS-3", "INS-4"]
    assert claims["INS-2"] == _RAJ_FIRST.issue_updates[0]
    assert claims["INS-3"] == _RAJ_FIRST.issue_updates[1]
    # INS-4 is what the answer says now.
    assert claims["INS-4"] == _RAJ_ANSWER.issue_updates[0]
    # "No blockers" from the first reply and the ETA from the answer both count.
    assert merged.blockers_answered
    assert merged.eta_answered
    assert merged.progress_note == (
        "INS-2 backfill is done code-wise, only waiting on the merge of insights-pipeline !1; "
        "INS-3 is merged (!2); INS-4 not started. "
        "INS-4 starts Monday once !1 is merged; wrapped up by midweek."
    )
    assert merged.parser_confident


def test_a_later_message_that_changes_an_earlier_fact_wins() -> None:
    first = CheckInSignals(
        progress_note="INS-3 is merged.",
        eta_change_days=1,
        eta_answered=True,
        issue_updates=(IssueClaim(issue_key="INS-3", claimed_done=True, claimed_state="merged"),),
    )
    correction = CheckInSignals(
        progress_note="Correction: INS-3 is not merged yet, !2 is still in review.",
        eta_change_days=3,
        issue_updates=(
            IssueClaim(
                issue_key="ins-3",
                claimed_done=False,
                claimed_state="in review",
                note="Still in review on !2.",
            ),
        ),
    )

    merged = merge_checkin_signals(first, correction)

    (claim,) = merged.issue_updates
    assert (claim.issue_key, claim.claimed_done, claim.claimed_state, claim.note) == (
        "INS-3",
        False,
        "in review",
        "Still in review on !2.",
    )
    assert merged.eta_change_days == 3


def test_a_mention_without_a_state_keeps_the_earlier_state_and_adds_its_note() -> None:
    first = CheckInSignals(
        progress_note="INS-2 done.",
        issue_updates=(
            IssueClaim(issue_key="INS-2", claimed_done=True, claimed_state="done", note="Done."),
        ),
    )
    answer = CheckInSignals(
        progress_note="The merge of !1 should land Monday.",
        issue_updates=(IssueClaim(issue_key="INS-2", note="Merge of !1 lands Monday."),),
    )

    (claim,) = merge_checkin_signals(first, answer).issue_updates

    assert (claim.claimed_done, claim.claimed_state) == (True, "done")
    assert claim.note == "Done. Merge of !1 lands Monday."


def test_eta_answers_and_confidence_across_messages() -> None:
    eta = CheckInSignals(progress_note="ETA slips 2 days.", eta_change_days=2, eta_answered=True)
    no_eta = CheckInSignals(progress_note="No blockers.", blockers_answered=True)
    garbled = CheckInSignals(progress_note="?!", parser_confident=False)

    merged = merge_checkin_signals(eta, no_eta)
    assert (merged.eta_change_days, merged.eta_answered, merged.blockers_answered) == (
        2,
        True,
        True,
    )
    # One message that could not be read keeps the whole check-in from confirming.
    assert merge_checkin_signals(merged, CheckInSignals(progress_note="ok")).parser_confident
    assert not merge_checkin_signals(merged, garbled).parser_confident
    assert not merge_checkin_signals(garbled, no_eta).parser_confident


def test_requests_are_kept_per_kind_and_person_and_the_later_one_wins() -> None:
    review = CrossPersonMention(raw_name="Noah", kind="review", note="Review CHK-8 on !1")
    dependency = CrossPersonMention(raw_name="Omar", kind="dependency", note="CHK-17 merge")
    noah_again = CrossPersonMention(
        raw_name="noah", kind="review", note="Re-review CHK-8", email="noah@acme.example"
    )

    merged = merge_checkin_signals(
        CheckInSignals(progress_note="a", requests=(review, dependency)),
        CheckInSignals(progress_note="b", requests=(noah_again,)),
    )

    assert merged.requests == (noah_again, dependency)


def test_blocker_fields_are_the_newest_messages_own() -> None:
    first = CheckInSignals(
        progress_note="Blocked on sandbox credentials.",
        blockers=("sandbox credentials",),
        blocker_reports=(BlockerReport(description="sandbox credentials"),),
        blockers_answered=True,
    )
    answer = CheckInSignals(
        progress_note="Credentials arrived.",
        resolved_blocker_ids=("blk-1",),
        blockers_answered=True,
    )

    merged = merge_checkin_signals(first, answer)

    # The blocker lifecycle carries and resolves blockers; the merge does not.
    assert (merged.blockers, merged.blocker_reports, merged.resolved_blocker_ids) == (
        (),
        (),
        ("blk-1",),
    )


def test_merging_the_same_message_twice_changes_nothing() -> None:
    once = merge_checkin_signals(_RAJ_FIRST, _RAJ_ANSWER)

    assert merge_checkin_signals(once, _RAJ_ANSWER) == once
