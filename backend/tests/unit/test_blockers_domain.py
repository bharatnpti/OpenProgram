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
