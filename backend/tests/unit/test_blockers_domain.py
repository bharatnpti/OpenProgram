"""Pure-domain tests for the blocker lifecycle (matching, minting, resolution)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from core.domain.blockers import (
    BlockerReconciliation,
    BlockerReport,
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
    normalize_blocker_key,
    reconcile_blockers,
    unattributed_blockers,
)

DAY_1 = date(2026, 1, 10)
DAY_2 = date(2026, 1, 11)
DAY_3 = date(2026, 1, 12)


def _blocker(
    *,
    blocker_id: str = "b-1",
    description: str = "waiting on DBA sign-off",
    work_item_id: str | None = None,
    pod_id: str | None = None,
    first_seen_on: date = DAY_1,
    last_seen_on: date = DAY_1,
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=blocker_id,
        developer_id="dev-1",
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        pod_id=pod_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen_on,
        last_seen_on=last_seen_on,
    )


def _mint_ids(*ids: str) -> Callable[[], str]:
    queue = list(ids)

    def mint() -> str:
        return queue.pop(0)

    return mint


def _reconcile(
    open_blockers: Sequence[DeveloperBlocker],
    reports: Sequence[BlockerReport],
    *,
    mode: ReconcileMode = ReconcileMode.CHECKIN,
    resolved_ids: Sequence[str] = (),
    mint: Callable[[], str] | None = None,
) -> BlockerReconciliation:
    return reconcile_blockers(
        open_blockers,
        reports,
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        mode=mode,
        source=BlockerSource.CHECKIN,
        resolved_blocker_ids=resolved_ids,
        source_correlation_id="corr-1",
        mint_id=mint or _mint_ids("m-1", "m-2", "m-3"),
    )


def test_normalize_blocker_key_folds_case_whitespace_and_trailing_punctuation() -> None:
    assert normalize_blocker_key("  Waiting   on\tDBA sign-off. ") == "waiting on dba sign-off"
    assert normalize_blocker_key("VPN access!!") == "vpn access"
    assert normalize_blocker_key("psp sandbox") == normalize_blocker_key("PSP  Sandbox.")


def test_reconcile_mints_new_blocker_with_first_and_last_seen_today() -> None:
    result = _reconcile((), (BlockerReport(description="new blocker", issue_key="PROJ-9"),))
    assert len(result.minted) == 1
    minted = result.minted[0]
    assert minted.blocker_id == "m-1"
    assert minted.first_seen_on == DAY_2
    assert minted.last_seen_on == DAY_2
    assert minted.work_item_id == "PROJ-9"
    assert minted.source_correlation_id == "corr-1"
    assert result.open_after == result.minted
    assert result.upserts == result.minted


def test_reconcile_matches_by_issue_key_before_description() -> None:
    prior = _blocker(description="old wording", work_item_id="PROJ-1")
    result = _reconcile(
        (prior,), (BlockerReport(description="completely new wording", issue_key="PROJ-1"),)
    )
    assert result.minted == ()
    assert len(result.upserts) == 1
    updated = result.upserts[0]
    assert updated.blocker_id == "b-1"
    assert updated.description == "completely new wording"
    assert updated.last_seen_on == DAY_2
    assert updated.first_seen_on == DAY_1


def test_reconcile_matches_by_normalized_description() -> None:
    prior = _blocker(description="Waiting on DBA sign-off")
    result = _reconcile((prior,), (BlockerReport(description="waiting on   dba sign-off."),))
    assert result.minted == ()
    assert result.upserts[0].blocker_id == "b-1"
    assert result.upserts[0].last_seen_on == DAY_2


def test_reconcile_fills_missing_attribution_without_overwriting_existing() -> None:
    attributed = _blocker(blocker_id="b-1", work_item_id="PROJ-1")
    bare = _blocker(blocker_id="b-2", description="vpn access")
    result = _reconcile(
        (attributed, bare),
        (
            BlockerReport(description="waiting on DBA sign-off", issue_key="OTHER-9"),
            BlockerReport(description="vpn access", pod_id="pod-a"),
        ),
    )
    by_id = {blocker.blocker_id: blocker for blocker in result.upserts}
    assert by_id["b-1"].work_item_id == "PROJ-1"  # never overwritten
    assert by_id["b-2"].pod_id == "pod-a"  # filled when empty


def test_reconcile_resolves_reported_ids_and_carries_rest_untouched() -> None:
    first = _blocker(blocker_id="b-1")
    second = _blocker(blocker_id="b-2", description="vpn access")
    result = _reconcile((first, second), (), resolved_ids=("b-1",))
    assert [blocker.blocker_id for blocker in result.resolved] == ["b-1"]
    assert result.resolved[0].resolved_on == DAY_2
    assert result.resolved[0].resolved_reason is BlockerResolutionReason.REPORTED_RESOLVED
    assert result.carried == (second,)
    assert second.last_seen_on == DAY_1  # carry-forward does not bump
    assert result.open_after == (second,)


def test_reconcile_resolve_all_mode_resolves_unmentioned() -> None:
    prior = _blocker()
    result = _reconcile((prior,), (), mode=ReconcileMode.RESOLVE_ALL)
    assert len(result.resolved) == 1
    assert result.resolved[0].resolved_reason is BlockerResolutionReason.REPORTED_RESOLVED
    assert result.open_after == ()


def test_reconcile_authoritative_set_resolves_omissions() -> None:
    kept = _blocker(blocker_id="b-1")
    dropped = _blocker(blocker_id="b-2", description="vpn access")
    result = _reconcile(
        (kept, dropped),
        (BlockerReport(description="waiting on DBA sign-off"),),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )
    resolved_ids = {blocker.blocker_id for blocker in result.resolved}
    assert resolved_ids == {"b-2"}
    assert result.resolved[0].resolved_reason is BlockerResolutionReason.OMITTED_IN_CORRECTION
    assert {blocker.blocker_id for blocker in result.open_after} == {"b-1"}


def test_reconcile_authoritative_empty_set_confirms_no_blockers() -> None:
    prior = _blocker()
    result = _reconcile((prior,), (), mode=ReconcileMode.AUTHORITATIVE_SET)
    assert result.resolved[0].resolved_reason is BlockerResolutionReason.CONFIRMED_NO_BLOCKERS


def test_reconcile_confirm_all_bumps_last_seen() -> None:
    prior = _blocker()
    result = _reconcile((prior,), (), mode=ReconcileMode.CONFIRM_ALL)
    assert result.upserts[0].last_seen_on == DAY_2
    assert result.upserts[0].resolved_on is None
    assert result.open_after == result.upserts


def test_reconcile_report_resolved_flag_resolves_match() -> None:
    prior = _blocker()
    result = _reconcile(
        (prior,), (BlockerReport(description="waiting on DBA sign-off", resolved=True),)
    )
    assert len(result.resolved) == 1
    assert result.open_after == ()


def test_reconcile_dedupes_duplicate_reports() -> None:
    result = _reconcile(
        (),
        (
            BlockerReport(description="vpn access"),
            BlockerReport(description="VPN  access."),
        ),
    )
    assert len(result.minted) == 1


def test_reconcile_is_idempotent_on_replay() -> None:
    prior = _blocker()
    reports = (BlockerReport(description="waiting on DBA sign-off", issue_key="PROJ-1"),)
    first = _reconcile((prior,), reports)
    replay = _reconcile(first.open_after, reports)
    assert replay.minted == ()
    assert replay.open_after == first.open_after


def test_is_open_on_uses_half_open_resolution_window() -> None:
    blocker = _blocker()
    resolved = _reconcile((blocker,), (), resolved_ids=("b-1",)).resolved[0]
    assert resolved.is_open_on(DAY_1) is True
    assert resolved.is_open_on(DAY_2) is False
    assert blocker.is_open_on(date(2026, 1, 9)) is False  # before first_seen_on


def test_unattributed_helper_filters_by_derived_attribution() -> None:
    bare = _blocker(blocker_id="b-1")
    with_item = _blocker(blocker_id="b-2", description="x", work_item_id="PROJ-1")
    with_pod = _blocker(blocker_id="b-3", description="y", pod_id="pod-a")
    assert unattributed_blockers((bare, with_item, with_pod)) == (bare,)


# ---- a correction names the blockers it restates by id ----------------------------------


def _on_one_work_item() -> tuple[DeveloperBlocker, DeveloperBlocker]:
    """Two open blockers on one work item, the older one listed first."""
    return (
        _blocker(
            blocker_id="b-review",
            description="waiting on code review",
            work_item_id="PROJ-1",
            first_seen_on=date(2026, 1, 5),
        ),
        _blocker(
            blocker_id="b-staging",
            description="staging is down",
            work_item_id="PROJ-1",
            first_seen_on=DAY_1,
        ),
    )


def test_correction_keeps_the_blocker_its_id_names_when_two_share_a_work_item() -> None:
    review, staging = _on_one_work_item()

    result = _reconcile(
        (review, staging),
        (BlockerReport(description="staging is down", issue_key="PROJ-1", blocker_id="b-staging"),),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    # Matched by work item first, the review blocker took the staging wording
    # and the staging blocker was closed as left out.
    assert [(item.blocker_id, item.description) for item in result.open_after] == [
        ("b-staging", "staging is down")
    ]
    assert result.open_after[0].first_seen_on == DAY_1
    assert [(item.blocker_id, item.resolved_reason) for item in result.resolved] == [
        ("b-review", BlockerResolutionReason.OMITTED_IN_CORRECTION)
    ]
    assert result.minted == ()


def test_correction_by_id_never_swaps_two_blockers_on_one_work_item() -> None:
    review, staging = _on_one_work_item()

    result = _reconcile(
        (review, staging),
        (
            BlockerReport(
                description="staging is still down", issue_key="PROJ-1", blocker_id="b-staging"
            ),
            BlockerReport(
                description="waiting on code review", issue_key="PROJ-1", blocker_id="b-review"
            ),
        ),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    kept = {item.blocker_id: item for item in result.open_after}
    assert kept["b-staging"].description == "staging is still down"
    assert kept["b-staging"].first_seen_on == DAY_1
    assert kept["b-review"].description == "waiting on code review"
    assert kept["b-review"].first_seen_on == date(2026, 1, 5)
    assert result.resolved == ()
    assert result.minted == ()


def test_correction_by_id_keeps_the_age_of_a_blocker_reworded_and_detached() -> None:
    prior = _blocker(
        blocker_id="b-1", description="staging is down", work_item_id="PROJ-1", first_seen_on=DAY_1
    )

    result = _reconcile(
        (prior,),
        (BlockerReport(description="staging is down, ops ticket filed", blocker_id="b-1"),),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    # Without the id, neither the work item nor the wording matched, so the
    # blocker restarted as a new one at age 0 and the old one was closed.
    assert [(item.blocker_id, item.first_seen_on) for item in result.open_after] == [("b-1", DAY_1)]
    assert result.open_after[0].description == "staging is down, ops ticket filed"
    assert result.minted == ()
    assert result.resolved == ()


def test_correction_by_id_keeps_two_blockers_worded_alike() -> None:
    first = _blocker(blocker_id="b-1", description="waiting on review", work_item_id="PROJ-1")
    second = _blocker(blocker_id="b-2", description="waiting on review", work_item_id="PROJ-2")

    result = _reconcile(
        (first, second),
        (
            BlockerReport(description="waiting on review", blocker_id="b-1"),
            BlockerReport(description="waiting on review", blocker_id="b-2", resolved=True),
        ),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    assert [item.blocker_id for item in result.open_after] == ["b-1"]
    assert [(item.blocker_id, item.resolved_reason) for item in result.resolved] == [
        ("b-2", BlockerResolutionReason.REPORTED_RESOLVED)
    ]


def test_correction_with_an_id_naming_no_open_blocker_is_matched_as_before() -> None:
    # A legacy status's blocker is served as legacy:<dev>:<n> but reconciled
    # as an unsaved shim:<dev>:<n> row, so its id names no open blocker.
    shim = _blocker(blocker_id="shim:dev-1:1", description="waiting on DBA sign-off")

    result = _reconcile(
        (shim,),
        (BlockerReport(description="waiting on DBA sign-off", blocker_id="legacy:dev-1:1"),),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    assert [(item.blocker_id, item.first_seen_on) for item in result.open_after] == [
        ("shim:dev-1:1", DAY_1)
    ]
    assert result.minted == ()


def test_reports_without_ids_still_match_by_work_item_then_wording() -> None:
    review, staging = _on_one_work_item()

    result = _reconcile(
        (review, staging),
        (
            BlockerReport(description="staging is down", blocker_id="b-staging"),
            # No id: matched by work item among the blockers no id claimed.
            BlockerReport(description="review requested again", issue_key="PROJ-1"),
        ),
        mode=ReconcileMode.AUTHORITATIVE_SET,
    )

    kept = {item.blocker_id: item.description for item in result.open_after}
    assert kept == {"b-staging": "staging is down", "b-review": "review requested again"}
    assert result.resolved == ()
