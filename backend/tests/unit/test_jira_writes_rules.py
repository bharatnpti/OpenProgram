"""The Jira write switches: every combination of master and kind, the defaults, the projects."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.domain.jira_writes import (
    KIND_ORDER,
    MASTER_OFF,
    CreateProjects,
    JiraWriteKind,
    JiraWrites,
    JiraWritesError,
    JiraWritesUpdate,
    SettingSource,
    StoredJiraWrites,
    apply_update,
    changes_between,
    resolve_jira_writes,
    stored_from_json,
    stored_json,
    validated_create_projects,
)

AT = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
CHECKINS = JiraWriteKind.CHECKIN_UPDATES
CONSOLE = JiraWriteKind.CONSOLE_MOVES
CREATE = JiraWriteKind.READINESS_CREATE


def _writes(
    master: bool | None = None,
    *,
    env: bool = False,
    env_set: bool = False,
    kinds: dict[JiraWriteKind, bool] | None = None,
    projects: CreateProjects | None = None,
    legacy: bool | None = None,
) -> JiraWrites:
    stored = (
        StoredJiraWrites(kinds=kinds or {}, create_projects=projects)
        if kinds is not None or projects is not None
        else None
    )
    return resolve_jira_writes(
        master_override=master,
        env_master=env,
        env_master_set=env_set,
        stored=stored,
        legacy_readiness_create=legacy,
    )


def test_nothing_set_keeps_what_each_kind_did_before_its_switch() -> None:
    writes = _writes()

    assert (writes.master.on, writes.master.source) == (False, SettingSource.DEFAULT)
    assert writes.kind(CHECKINS).on is True
    assert writes.kind(CONSOLE).on is True
    assert writes.kind(CREATE).on is False
    assert {writes.kind(kind).source for kind in KIND_ORDER} == {SettingSource.DEFAULT}
    assert writes.create_projects == CreateProjects(own_project=True, projects=())
    assert writes.create_projects_source is SettingSource.DEFAULT


def test_master_source_is_admin_then_env_then_default() -> None:
    assert _writes(env=True, env_set=True).master.source is SettingSource.ENV
    assert _writes(env=True, env_set=True).master.on is True
    assert _writes(False, env=True, env_set=True).master.source is SettingSource.ADMIN
    assert _writes(False, env=True, env_set=True).master.on is False
    assert _writes(env=False, env_set=False).master.source is SettingSource.DEFAULT


@pytest.mark.parametrize("kind", KIND_ORDER)
def test_master_off_refuses_every_kind_even_when_the_kind_is_on(kind: JiraWriteKind) -> None:
    writes = _writes(False, kinds={kind: True})

    assert writes.kind(kind).on is True
    assert writes.allows(kind) is False
    assert writes.refusal(kind) == MASTER_OFF


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        (CHECKINS, "Updating tickets from check-ins is off for this tenant."),
        (CONSOLE, "Moving tickets from task updates is off for this tenant."),
        (CREATE, "Creating release-readiness issues is off for this tenant."),
    ],
)
def test_master_on_with_a_kind_off_refuses_that_kind_only(kind: JiraWriteKind, words: str) -> None:
    writes = _writes(True, kinds={kind: False})

    assert writes.allows(kind) is False
    assert writes.refusal(kind) == words
    others = [other for other in KIND_ORDER if other is not kind and other is not CREATE]
    assert all(writes.allows(other) for other in others)


def test_master_on_and_check_ins_never_set_counts_as_on() -> None:
    writes = _writes(True)

    assert writes.allows(CHECKINS) is True
    assert writes.allows(CONSOLE) is True
    assert writes.refusal(CHECKINS) is None
    # Creating issues stays off until someone turns it on.
    assert writes.allows(CREATE) is False


def test_the_readiness_settings_switch_counts_until_one_is_set_here() -> None:
    legacy_on = _writes(True, legacy=True)
    overridden = _writes(True, kinds={CREATE: False}, legacy=True)

    assert (legacy_on.kind(CREATE).on, legacy_on.kind(CREATE).source) == (
        True,
        SettingSource.ADMIN,
    )
    assert legacy_on.allows(CREATE) is True
    assert overridden.allows(CREATE) is False


def test_by_default_only_the_scopes_own_project_is_allowed() -> None:
    writes = _writes(True, kinds={CREATE: True})

    assert writes.create_refusal("CHK", {"CHK"}) is None
    assert writes.create_refusal("chk", {"CHK"}) is None
    assert writes.create_refusal("SEC", {"CHK"}) == (
        "This draft is for Jira project SEC, where OpenProgram may not create issues. "
        "It may create them in CHK."
    )
    assert writes.create_refusal("", {"CHK"}) is not None


def test_listed_projects_are_allowed_besides_or_instead_of_the_own_one() -> None:
    both = _writes(True, kinds={CREATE: True}, projects=CreateProjects(projects=("SEC",)))
    only_listed = _writes(
        True, kinds={CREATE: True}, projects=CreateProjects(own_project=False, projects=("SEC",))
    )

    assert both.create_refusal("SEC", {"CHK"}) is None
    assert both.create_refusal("CHK", {"CHK"}) is None
    assert both.create_projects_source is SettingSource.ADMIN
    assert only_listed.create_refusal("SEC", {"CHK"}) is None
    assert only_listed.create_refusal("CHK", {"CHK"}) == (
        "This draft is for Jira project CHK, where OpenProgram may not create issues. "
        "It may create them in SEC."
    )
    assert both.create_refusal("OPS", {"CHK", "PAY"}) == (
        "This draft is for Jira project OPS, where OpenProgram may not create issues. "
        "It may create them in CHK, PAY and SEC."
    )


def test_a_switch_refusal_comes_before_the_project() -> None:
    assert _writes(False, kinds={CREATE: True}).create_refusal("SEC", set()) == MASTER_OFF
    assert _writes(True).create_refusal("CHK", {"CHK"}) == (
        "Creating release-readiness issues is off for this tenant."
    )


def test_project_keys_are_tidied_and_checked() -> None:
    assert validated_create_projects(True, [" sec ", "SEC", "ops", ""]) == CreateProjects(
        own_project=True, projects=("SEC", "OPS")
    )
    with pytest.raises(JiraWritesError, match="capital letters and digits, such as CHK: SE C"):
        validated_create_projects(True, ["SE C"])
    with pytest.raises(JiraWritesError, match="at most 20"):
        validated_create_projects(True, [f"P{index:02d}" for index in range(21)])
    with pytest.raises(JiraWritesError, match="Allow at least one project"):
        validated_create_projects(False, [])


def test_an_update_changes_only_what_it_gives() -> None:
    stored = StoredJiraWrites(kinds={CHECKINS: False}, create_projects=CreateProjects())

    updated = apply_update(stored, JiraWritesUpdate(kinds={CREATE: True}))

    assert updated.kinds == {CHECKINS: False, CREATE: True}
    assert updated.create_projects == CreateProjects()
    assert JiraWritesUpdate().empty is True


def test_a_change_row_for_every_value_or_source_that_changed() -> None:
    before = _writes()
    after = _writes(True, kinds={CHECKINS: True, CREATE: True})

    changes = changes_between(before, after, tenant_id="demo", at=AT, actor="U1001")

    assert [(c.setting, c.before, c.after, c.before_source) for c in changes] == [
        ("master", False, True, SettingSource.DEFAULT),
        # Same value, but pinned by an admin now: a later default change won't move it.
        ("checkin_updates", True, True, SettingSource.DEFAULT),
        ("readiness_create", False, True, SettingSource.DEFAULT),
    ]
    assert changes_between(after, after, tenant_id="demo", at=AT, actor="U1001") == []


def test_the_stored_shape_round_trips_and_holds_only_what_was_set() -> None:
    stored = StoredJiraWrites(
        kinds={CREATE: False}, create_projects=CreateProjects(own_project=False, projects=("SEC",))
    )

    shape = stored_json(stored)

    assert shape == {
        "readiness_create": False,
        "create_projects": {"own_project": False, "projects": ["SEC"]},
    }
    assert stored_from_json(shape) == stored
    assert stored_from_json(None) is None
    assert stored_from_json({"checkin_updates": "yes"}) == StoredJiraWrites()
