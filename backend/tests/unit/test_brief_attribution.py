"""Who a brief says did what, checked against the facts (N51-N53), and its structure (N58).

The three known errors of the briefs, told with neutral names:

- N51: an exec brief gave a cleared blocker to the person it waited on, not
  to the person who reported it. Here: Ada's blocker, waiting on Ben's SHOP-2.
- N52: a pod brief named a merge request's opener as its merger. Here: Ben
  opened web !7, Cleo merged it (her merge commit says so).
- N53: briefs stated ETA slips nobody reported: durations misread as changes
  before the N45 parser, and an earlier change tied to a later check-in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from core.application.brief_facts import (
    BriefFacts,
    BriefInputs,
    BriefScope,
    StatusFacts,
    build_brief_facts,
)
from core.application.brief_grounding import (
    compose_brief,
    fallback_brief,
    ground_brief,
    repeats,
    without_clock,
)
from core.application.portfolio_feed_service import PortfolioFeedItemView
from core.domain.brief import brief_structure, structured_body
from core.domain.graph import EntityRef, JsonScalar, NodeKind, Task
from core.domain.status import CheckInDay

AS_OF = datetime(2026, 3, 9, 10, 0, tzinfo=UTC)
ADA, BEN, CLEO = "U0EXAMPLEA", "U0EXAMPLEB", "U0EXAMPLEC"
PEOPLE = {ADA: "Ada Lind", BEN: "Ben Okafor", CLEO: "Cleo Morales"}
STATUS = StatusFacts(
    sentence="Status of 3 team members, 1 pod and the program: 3 green, 2 amber.",
    counts={"green": 3, "amber": 2, "red": 0, "unknown": 0},
)
HEADLINE = "Amber: 1 of 3 updates is partial: Ben Okafor hasn't confirmed blockers or an ETA."
# What a free-text brief adds when it left out a blocker the window reported (N38).
BLOCKER_SENTENCE = (
    "Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben Okafor for SHOP-2), "
    "cleared at 06:06 UTC. No blocker is open now."
)


def _first(body: str) -> str:
    """A free-text brief's own sentences, before the blocker sentence it is given."""
    return body.removesuffix(BLOCKER_SENTENCE).strip()


def _item(
    source: str,
    kind: NodeKind,
    entity_id: str,
    hour: int,
    minute: int,
    line: str,
    person: str | None = None,
    **details: JsonScalar,
) -> PortfolioFeedItemView:
    return PortfolioFeedItemView(
        source=source,
        kind=source,
        summary=line,
        entity_ref=EntityRef(tenant_id="demo", kind=kind, id=entity_id),
        observed_at=AS_OF.replace(hour=hour, minute=minute),
        details=details,
        person_name=PEOPLE.get(person or "") if person else None,
    )


def _task(key: str, title: str, state: str, status: str) -> Task:
    return Task(
        tenant_id="demo",
        id=key,
        name=title,
        metadata={"key": key, "state": state, "status": status},
    )


def _checkin(
    person: str, hour: int, minute: int, *, blockers: int = 0, **details: JsonScalar
) -> PortfolioFeedItemView:
    return _item(
        "checkin",
        NodeKind.DEVELOPER,
        person,
        hour,
        minute,
        f"Check-in updated for {PEOPLE[person]}",
        person,
        blocker_count=blockers,
        **details,
    )


def _facts(*, eta_checked: bool = True, headline: str | None = HEADLINE) -> BriefFacts:
    """Ada waited on Ben's SHOP-2 (06:05, cleared 06:06); Ben opened web !7, Cleo merged it.

    Ben's 06:10 check-in moved his ETA a day; his 09:05 one, his latest, was
    partial and moved nothing. Ada opened shop !3 for SHOP-8; nobody recorded
    who merged it. Cleo's review ask names "merge !4", her open web !4.
    """
    gitlab = "https://git.example.test/acme/{repo}/-/merge_requests/{number}"
    items = [
        _checkin(ADA, 6, 5, blockers=1, status_source="confirmed"),
        _checkin(ADA, 9, 0, status_source="confirmed"),
        _item(
            "cross_person_request",
            NodeKind.DEVELOPER,
            BEN,
            6,
            6,
            "Cross-person dependency resolved",
            BEN,
            request_id="xreq-wait",
            transition="resolved",
            dependency_kind="waiting_on",
            reporter_id=ADA,
            reporter_name="Ada Lind",
            referenced_person_id=BEN,
            referenced_person_name="Ben Okafor",
            summary="Finish the client upgrade for SHOP-2",
        ),
        _checkin(
            BEN,
            6,
            10,
            status_source="confirmed",
            eta_change_days=1,
            eta_change_checked=eta_checked,
        ),
        _checkin(BEN, 9, 5, status_source="partial", eta_change_checked=eta_checked),
        _item(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            BEN,
            6,
            6,
            "PR 7 in acme/web merged: SHOP-2 Upgrade the client",
            BEN,
            repo="acme/web",
            id="7",
            merged=True,
            web_url=gitlab.format(repo="web", number=7),
        ),
        _item(
            "vcs_commit",
            NodeKind.DEVELOPER,
            CLEO,
            6,
            6,
            "Commit 1a2b3c4 in acme/web: Merge branch 'SHOP-2-upgrade' into 'main'\n\n"
            "SHOP-2 Upgrade the client\n\nSee merge request acme/web!7",
            CLEO,
            repo="acme/web",
            sha="1a2b3c4d",
        ),
        _item(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            ADA,
            7,
            0,
            "PR 3 in acme/shop merged: SHOP-8 Payment form",
            ADA,
            repo="acme/shop",
            id="3",
            merged=True,
        ),
        _item(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            CLEO,
            8,
            0,
            "PR 4 in acme/web updated: SHOP-11 Cart breakdown",
            CLEO,
            repo="acme/web",
            id="4",
            merged=False,
            web_url=gitlab.format(repo="web", number=4),
        ),
        _item(
            "cross_person_request",
            NodeKind.DEVELOPER,
            CLEO,
            8,
            30,
            "Cross-person review needs PM resolution",
            CLEO,
            request_id="xreq-review",
            transition="needs_resolution",
            dependency_kind="needs_review",
            reporter_id=CLEO,
            reporter_name="Cleo Morales",
            summary="Needs a reviewer assigned to merge !4",
        ),
    ]
    return build_brief_facts(
        BriefInputs(
            label="Executive brief",
            scope_name="portfolio",
            as_of=AS_OF,
            since=AS_OF.replace(day=2),
            status=STATUS,
            scope=BriefScope(),
            items=items,
            tasks={
                task.id: task
                for task in (
                    _task("SHOP-2", "Upgrade the client", "in_progress", "In Progress"),
                    _task("SHOP-8", "Payment form", "done", "Done"),
                    _task("SHOP-11", "Cart breakdown", "in_progress", "In Progress"),
                )
            },
            people=PEOPLE,
            repos=frozenset({"acme/web", "acme/shop"}),
            open_blockers={ADA: 0, BEN: 0, CLEO: 0},
            checkins_today=tuple(
                CheckInDay(
                    developer_id=person,
                    first_asked_at=AS_OF.replace(hour=6),
                    first_replied_at=AS_OF.replace(hour=6, minute=5),
                )
                for person in (ADA, BEN, CLEO)
            ),
            headline=headline,
        )
    )


# ---- what the model is given ----------------------------------------------------------


def test_the_facts_say_who_reported_each_blocker_and_who_merged_each_request() -> None:
    facts = _facts()

    assert (
        "Blockers: Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben "
        "Okafor for SHOP-2), cleared at 06:06 UTC. No blocker is open now. Nobody else "
        "reported a blocker in the window."
    ) in facts.context
    assert facts.blocker_people == {"Ada Lind"}
    # The request's own fact knows who opened it; the merge commit, who merged it.
    assert (
        "web !7 'SHOP-2 Upgrade the client' opened by Ben Okafor, merged by Cleo Morales"
    ) in facts.context
    assert (
        "merge request 3 in shop 'SHOP-8 Payment form' opened by Ada Lind, merged (who "
        "merged it is not recorded)"
    ) in facts.context
    # A merge request a request names by number alone, said in full.
    assert '"Needs a reviewer assigned to merge !4"), that is web !4 (SHOP-11)' in facts.context
    assert "ETA changes reported: Ben Okafor +1 day (check-in at 06:10 UTC, not their latest)." in (
        facts.context
    )
    assert "Cleo Morales's review request for web !4 (SHOP-11) needs a PM to resolve it." in (
        facts.action_lines
    )


# ---- N51: whose blocker -----------------------------------------------------------------


def test_n51_a_cleared_blocker_is_never_given_to_the_person_it_waited_on() -> None:
    facts = _facts()

    grounded = ground_brief(
        "Ben Okafor's check-in is partial; his blocker was cleared and his ETA was updated, "
        "but confirmation is still pending.",
        facts,
    )

    # The false clause goes; the true clauses stay, and the true blocker is said.
    assert grounded.body == (
        "Ben Okafor's check-in is partial; confirmation is still pending. "
        "Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben Okafor for "
        "SHOP-2), cleared at 06:06 UTC. No blocker is open now."
    )
    assert any(
        reason.startswith("gives Ben Okafor a blocker no check-in of theirs reported")
        for reason in grounded.replaced
    )


def test_n51_a_blocker_said_of_its_reporter_stays() -> None:
    grounded = ground_brief("Ada Lind's blocker on SHOP-2 cleared at 06:06 UTC.", _facts())

    assert grounded.body == "Ada Lind's blocker on SHOP-2 cleared at 06:06 UTC."
    assert not grounded.replaced


# ---- N52: who merged --------------------------------------------------------------------


def test_n52_a_merge_is_said_of_whoever_merged_it_not_of_its_opener() -> None:
    facts = _facts()

    renamed = ground_brief("SHOP-2 was merged by Ben Okafor.", facts)
    subject = ground_brief("Ben Okafor merged SHOP-2, unblocking Ada Lind.", facts)
    unknown = ground_brief("Ada Lind merged SHOP-8 this morning.", facts)

    assert _first(renamed.body) == "SHOP-2 was merged by Cleo Morales."
    assert _first(subject.body) == "Cleo Morales merged SHOP-2, unblocking Ada Lind."
    # Nobody recorded who merged shop !3: the claim goes, the true sentence stays.
    assert _first(unknown.body) == "SHOP-8 (merge request 3 in shop) was merged."


# ---- N53: whose ETA, and slips nobody reported ------------------------------------------


def test_n53_an_eta_change_read_before_the_duration_check_is_no_eta_change() -> None:
    """Pre-N45 facts: "2-3 days" stored as +2. Their numbers are never stated."""
    facts = _facts(eta_checked=False)
    assert facts.eta_changes == {}
    assert "ETA changes reported: none." in facts.context

    grounded = ground_brief(
        "Ben Okafor's ETA slipped by a day. ETA extensions for Ada Lind and Ben Okafor. "
        "One team member had an ETA adjustment of +1 day. SHOP-8 is done in the tracker.",
        facts,
    )

    assert _first(grounded.body) == "SHOP-8 is done in the tracker."


def test_n53_an_eta_change_is_said_only_of_its_person_and_its_check_in() -> None:
    facts = _facts()

    kept = ground_brief("Ben Okafor's ETA moved by a day.", facts)
    wrong_person = ground_brief("Ada Lind's ETA moved by a day.", facts)
    stale = ground_brief(
        "Ben Okafor's partial check-in came with an ETA increase of one day.", facts
    )

    assert _first(kept.body) == "Ben Okafor's ETA moved by a day."
    assert _first(wrong_person.body) == ""
    assert _first(stale.body) == ""
    assert stale.replaced == (
        "ties an earlier ETA change to the latest check-in: Ben Okafor's partial check-in "
        "came with an ETA increase of one day.",
    )


# ---- the structured brief ---------------------------------------------------------------


def test_a_structured_answer_is_grounded_part_by_part_and_kept_plain_and_short() -> None:
    facts = _facts()
    answer = json.dumps(
        {
            "verdict": "Amber: Ben Okafor's update is partial (asked 06:00 UTC).",
            "bullets": [
                "Ben Okafor's update is partial and unconfirmed.",
                "3 green and 2 amber statuses need attention.",
                "Cleo Morales needs a PM to assign a reviewer for merge !4 by Tuesday.",
                "Ada Lind's blocker on SHOP-2 cleared at 06:06 UTC.",
                "No blockers are open anywhere.",
                "SHOP-2 was merged by Ben Okafor.",
            ],
        }
    )

    composed = compose_brief(answer, facts)

    assert composed.structured
    # One clock per page: the brief names no time; the page shows it locally.
    assert composed.verdict == "Amber: Ben Okafor's update is partial."
    assert composed.bullets == (
        # Its repo and ticket, never "!4" alone.
        "Cleo Morales needs a PM to assign a reviewer for web !4 (SHOP-11) by Tuesday.",
        "Ada Lind's blocker on SHOP-2 cleared.",
        "SHOP-2 was merged by Cleo Morales.",
    )
    # Dropped: the bullet repeating the verdict, the colour count (jargon),
    # and the "no blockers" filler while none is open.
    assert any("counts statuses by colour" in reason for reason in composed.dropped)
    assert brief_structure(composed.body) is not None
    assert brief_structure(composed.body) == brief_structure(
        structured_body(composed.verdict, composed.bullets)
    )


def test_a_structured_brief_keeps_at_most_four_bullets() -> None:
    answer = json.dumps(
        {
            "verdict": "Amber: Ben Okafor's update is partial.",
            "bullets": [f"SHOP-{n} needs a review from Cleo Morales." for n in (2, 8, 11)]
            + ["Ada Lind waits on Cleo Morales.", "Cleo Morales owes Ada Lind a review."],
        }
    )

    composed = compose_brief(answer, _facts())

    assert len(composed.bullets) == 4


def test_a_verdict_the_facts_take_away_falls_back_to_the_days_headline() -> None:
    facts = _facts()
    answer = json.dumps(
        {"verdict": "Amber: SHOP-99 is late.", "bullets": ["Ada Lind waits on Cleo Morales."]}
    )

    composed = compose_brief(answer, facts)

    assert composed.verdict == HEADLINE
    # Short of two bullets: filled from who-must-act lines of the facts.
    assert composed.bullets == (
        "Ada Lind waits on Cleo Morales.",
        "Cleo Morales's review request for web !4 (SHOP-11) needs a PM to resolve it.",
    )


def test_a_prose_answer_becomes_bullets_under_the_days_headline() -> None:
    composed = compose_brief(
        "Ada Lind waits on Cleo Morales for a review. SHOP-8 is done in the tracker.", _facts()
    )

    assert not composed.structured
    assert composed.verdict == HEADLINE
    assert composed.bullets == (
        "Ada Lind waits on Cleo Morales for a review.",
        "SHOP-8 is done in the tracker.",
    )


def test_with_nothing_usable_the_brief_is_built_from_the_facts_alone() -> None:
    composed = fallback_brief(_facts(headline=None))

    assert composed.verdict == STATUS.sentence
    assert composed.bullets == (
        "Cleo Morales's review request for web !4 (SHOP-11) needs a PM to resolve it.",
        "SHOP-2 is merged but still open in the tracker.",
    )
    assert brief_structure(composed.body) is not None


def test_clock_times_and_what_introduced_them_come_out() -> None:
    assert without_clock(
        "Amber: no one has answered today's check-in yet (0 of 10, asked 07:46 UTC), so "
        "today's statuses are inferred."
    ) == (
        "Amber: no one has answered today's check-in yet (0 of 10), so today's statuses "
        "are inferred."
    )
    assert without_clock(
        "Ada's check-in at 4 Oct 06:05 UTC reported 1 blocker, cleared at 06:06 UTC."
    ) == ("Ada's check-in reported 1 blocker, cleared.")
    assert without_clock("Asked 13:16 IST.") == "Asked."
    assert without_clock("SHOP-12 needs review by Tuesday.") == "SHOP-12 needs review by Tuesday."


def test_a_bullet_repeats_the_verdict_unless_it_names_someone_new() -> None:
    facts = _facts()
    verdict = "Amber: no one has answered today's check-in yet (0 of 3)."

    assert repeats("All 3 team members still need to answer today's check-in.", verdict, facts)
    assert not repeats("Ada Lind and Ben Okafor must answer today's check-in.", verdict, facts)
    assert not repeats("SHOP-8 is done in the tracker.", verdict, facts)


def test_a_stored_body_reads_as_structured_only_in_the_structured_shape() -> None:
    structured = brief_structure("Amber: one update is partial.\n- Ben confirms.\n- Cleo reviews.")
    assert structured is not None
    assert structured.verdict == "Amber: one update is partial."
    assert structured.bullets == ("Ben confirms.", "Cleo reviews.")
    # An older free-text brief: the console splits it into sentences instead.
    assert brief_structure("Since Monday, three people checked in. Ada waits on Ben.") is None
    assert brief_structure("- a bullet first\n- another") is None
    assert brief_structure("Verdict.\n- \n- b") is None
