"""The deterministic check of a model-written brief against its facts (N38, N39)."""

from __future__ import annotations

from datetime import UTC, datetime

from core.application.brief_facts import (
    BriefFacts,
    BriefInputs,
    BriefScope,
    StatusFacts,
    build_brief_facts,
)
from core.application.brief_grounding import ground_brief
from core.application.portfolio_feed_service import PortfolioFeedItemView
from core.domain.graph import EntityRef, JsonScalar, NodeKind, Task

AS_OF = datetime(2026, 1, 12, 9, 15, tzinfo=UTC)
ADA, BEN, CY = "U0123ABCD", "U0123EFGH", "U0123IJKL"
STATUS = StatusFacts(
    sentence="Check-ins: 2 confirmed, 0 partial, 0 stale, 0 missing across 2 member(s).",
    counts={"confirmed": 2, "partial": 0, "stale": 0, "missing": 0},
)


def _item(
    source: str,
    kind: NodeKind,
    entity_id: str,
    at: datetime,
    line: str,
    person_name: str | None = None,
    **details: JsonScalar,
) -> PortfolioFeedItemView:
    return PortfolioFeedItemView(
        source=source,
        kind=source,
        summary=line,
        entity_ref=EntityRef(tenant_id="demo", kind=kind, id=entity_id),
        observed_at=at,
        details=details,
        person_name=person_name,
    )


def _task(key: str, title: str, state: str, status: str) -> Task:
    return Task(
        tenant_id="demo",
        id=key,
        name=title,
        metadata={"key": key, "state": state, "status": status},
    )


def _web_pod_facts(*, ada_open_blockers: int = 0) -> BriefFacts:
    """Web Pod (Ada, Ben): SHOP-1 done, SHOP-2 merged with its ticket open.

    Payments Pod's SHOP-4 and its member Cleo are in the tenant, not in scope.
    Ada's 06:05 check-in reported a blocker; her wait on Ben ended at 06:06.
    """
    at = AS_OF.replace(hour=6, minute=5)
    items = [
        _item(
            "issue",
            NodeKind.TASK,
            "SHOP-1",
            at.replace(hour=3),
            "Issue SHOP-1 moved to done: Checkout form",
            key="SHOP-1",
            state="done",
        ),
        _item(
            "issue",
            NodeKind.TASK,
            "SHOP-4",
            at.replace(hour=4),
            "Issue SHOP-4 moved to done: Payment retries",
            key="SHOP-4",
            state="done",
        ),
        _item(
            "checkin",
            NodeKind.DEVELOPER,
            ADA,
            at,
            "Check-in updated for Ada Lind: confirmed, 1 blocker(s)",
            "Ada Lind",
            status_source="confirmed",
            blocker_count=1,
        ),
        _item(
            "checkin",
            NodeKind.DEVELOPER,
            BEN,
            at,
            "Check-in updated for Ben Okafor: confirmed, 0 blocker(s)",
            "Ben Okafor",
            status_source="confirmed",
            blocker_count=0,
        ),
        _item(
            "checkin",
            NodeKind.DEVELOPER,
            CY,
            at,
            "Check-in updated for Cleo Morales: confirmed, 0 blocker(s)",
            "Cleo Morales",
            status_source="confirmed",
            blocker_count=0,
        ),
        _item(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            BEN,
            at.replace(minute=6),
            "PR 7 in acme/web merged: SHOP-2 Upgrade HTTP client",
            "Ben Okafor",
            repo="acme/web",
            id="7",
            merged=True,
        ),
        _item(
            "cross_person_request",
            NodeKind.DEVELOPER,
            BEN,
            at.replace(minute=6),
            "Cross-person dependency resolved: Ben Okafor completed the upgrade",
            "Ben Okafor",
            request_id="xreq-wait",
            transition="resolved",
            dependency_kind="waiting_on",
            reporter_id=ADA,
            reporter_name="Ada Lind",
            referenced_person_id=BEN,
            referenced_person_name="Ben Okafor",
            summary="Finish the HTTP client upgrade for SHOP-2",
        ),
    ]
    return build_brief_facts(
        BriefInputs(
            label="Daily pod summary",
            scope_name="Web Pod",
            as_of=AS_OF,
            since=AS_OF.replace(day=11),
            status=STATUS,
            scope=BriefScope(
                member_ids=frozenset({ADA, BEN}),
                member_names=("Ada Lind", "Ben Okafor"),
                issue_ids=frozenset({"SHOP-1", "SHOP-2"}),
                node_ids=frozenset({"acme/web", "SHOP-1", "SHOP-2"}),
                repos=frozenset({"acme/web"}),
            ),
            items=items,
            tasks={
                task.id: task
                for task in (
                    _task("SHOP-1", "Checkout form", "done", "Done"),
                    _task("SHOP-2", "Upgrade HTTP client", "in_progress", "In Progress"),
                    _task("SHOP-4", "Payment retries", "done", "Done"),
                )
            },
            people={ADA: "Ada Lind", BEN: "Ben Okafor", CY: "Cleo Morales"},
            repos=frozenset({"acme/web", "acme/pay"}),
            open_blockers={ADA: ada_open_blockers, BEN: 0},
            scope_ref="pod:pod-web",
        )
    )


def test_grounding_drops_sentences_naming_what_the_pod_facts_do_not() -> None:
    facts = _web_pod_facts()
    assert facts.issue_keys == {"SHOP-1", "SHOP-2"}
    assert "Cleo Morales" not in facts.context

    grounded = ground_brief(
        "Ada Lind and Ben Okafor checked in. SHOP-1 is done in the tracker. "
        "SHOP-4 payment retries were completed. Cleo Morales confirmed his check-in. "
        "Cleo reported nothing new. Two reviews were superseded and rescheduled. "
        "SHOP-99 was opened. The work slipped by a week. "
        "The SHA-256 checksum step was added.",
        facts,
    )

    assert grounded.body == (
        "Ada Lind and Ben Okafor checked in. SHOP-1 is done in the tracker. "
        "The SHA-256 checksum step was added. "
        "Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben Okafor for "
        "SHOP-2), cleared at 06:06 UTC. "
        "No blocker is open now."
    )
    reasons = [reason.split(":")[0] for reason in grounded.dropped]
    assert reasons == [
        "names SHOP-4, which the brief's facts do not",
        "names Cleo Morales, whom the brief's facts do not",
        "names Cleo Morales, whom the brief's facts do not",
        "says 'rescheduled', which no fact says",
        "names SHOP-99, which the brief's facts do not",
        "speaks of a delay, but no ETA moved later",
    ]


def test_grounding_corrects_done_claims_blocker_denials_and_counts() -> None:
    facts = _web_pod_facts()
    merged_open = (
        "SHOP-2 (Upgrade HTTP client) is merged in merge request 7 in web, ticket still open "
        "(In Progress in the tracker)."
    )
    blockers = (
        "Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben Okafor for "
        "SHOP-2), cleared at 06:06 UTC. "
        "No blocker is open now."
    )

    grounded = ground_brief(
        "Check-ins: 5 confirmed. SHOP-1 and SHOP-2 were completed. "
        "The HTTP client upgrade was completed. SHOP-2 is merged but not yet done. "
        "SHOP-1 was completed, while SHOP-2 was merged but remains open. "
        "All check-ins report no blockers, and a dependency was resolved. "
        "No blockers are open now.",
        facts,
    )

    assert grounded.body == " ".join(
        [
            STATUS.sentence,
            "SHOP-1 (Checkout form) is done in the tracker.",
            merged_open,
            "SHOP-2 is merged but not yet done.",
            "SHOP-1 was completed, while SHOP-2 was merged but remains open.",
            blockers,
            "No blockers are open now.",
        ]
    )
    assert len(grounded.replaced) == 4
    assert not grounded.dropped


def test_grounding_replaces_no_blockers_while_one_is_open() -> None:
    facts = _web_pod_facts(ada_open_blockers=1)
    assert facts.blocker_sentence == (
        "Ada Lind's check-in at 06:05 UTC reported 1 blocker (waiting on Ben Okafor for "
        "SHOP-2), 1 still open. "
        "Open now: Ada Lind 1."
    )

    grounded = ground_brief("No open blockers remain. SHOP-1 is done in the tracker.", facts)

    assert grounded.body == f"{facts.blocker_sentence} SHOP-1 is done in the tracker."
