"""Jira writes: the tenant's master switch, one switch per kind of write, and where issues go.

Pure rules, no I/O. The master switch is the tenant write-back switch: off,
nothing writes to Jira, whatever else is on. Each kind's switch is read only
while the master is on:

- ``checkin_updates``: a check-in's transitions and comments, still behind each
  member's consent, the owner-only rule and the open merge request hold.
- ``console_moves``: a person's "Also move in Jira" tick on an update of their own
  task, behind the same consent, owner and merge request rules.
- ``readiness_create``: a person's press of Create on one release readiness draft.

A kind nobody has set keeps what it did before its switch existed: the two
kinds that wrote are on, creating issues is off (or what the release readiness
settings said, when an admin saved them before this switch existed).
``CreateProjects`` limits the Jira projects release readiness may create in; by
default only the scope's own project.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from core.domain.errors import OpenProgramError

MAX_CREATE_PROJECTS = 20
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")


class JiraWritesError(OpenProgramError):
    """A Jira writes setting that cannot be saved (422)."""


class JiraWriteKind(StrEnum):
    CHECKIN_UPDATES = "checkin_updates"
    CONSOLE_MOVES = "console_moves"
    READINESS_CREATE = "readiness_create"


#: The order the console lists the kinds in.
KIND_ORDER: tuple[JiraWriteKind, ...] = (
    JiraWriteKind.CHECKIN_UPDATES,
    JiraWriteKind.CONSOLE_MOVES,
    JiraWriteKind.READINESS_CREATE,
)

#: A kind's value while nobody has set it. On for the kinds that wrote before
#: their switch existed, so adding the switch changes nothing; off for creating.
KIND_DEFAULTS: Mapping[JiraWriteKind, bool] = MappingProxyType(
    {
        JiraWriteKind.CHECKIN_UPDATES: True,
        JiraWriteKind.CONSOLE_MOVES: True,
        JiraWriteKind.READINESS_CREATE: False,
    }
)

#: The change log's name for the master switch and for the project allow-list.
MASTER = "master"
CREATE_PROJECTS = "create_projects"

MASTER_OFF = "Jira writes are off for this tenant."
KIND_OFF: Mapping[JiraWriteKind, str] = MappingProxyType(
    {
        JiraWriteKind.CHECKIN_UPDATES: "Updating tickets from check-ins is off for this tenant.",
        JiraWriteKind.CONSOLE_MOVES: "Moving tickets from task updates is off for this tenant.",
        JiraWriteKind.READINESS_CREATE: "Creating release-readiness issues is off for this tenant.",
    }
)


class SettingSource(StrEnum):
    """Where an effective value comes from."""

    #: Nobody set it: the built-in default.
    DEFAULT = "default"
    #: The deployment's environment (the master switch only).
    ENV = "env"
    #: An admin set it for this tenant.
    ADMIN = "admin"


@dataclass(frozen=True, kw_only=True)
class CreateProjects:
    """The Jira projects release readiness may create issues in.

    ``own_project``: the project of the scope the draft is for (a pod's: the
    projects it works in). ``projects``: other project keys, allowed as well.
    """

    own_project: bool = True
    projects: tuple[str, ...] = ()

    def allows(self, project_key: str, own_keys: Collection[str]) -> bool:
        key = project_key.strip().upper()
        if not key:
            return False
        return (self.own_project and key in own_keys) or key in self.projects

    def allowed_keys(self, own_keys: Iterable[str]) -> tuple[str, ...]:
        """Every key a draft of this scope may go to: its own (if allowed), then the others."""
        own = tuple(key for key in own_keys if key) if self.own_project else ()
        return tuple(dict.fromkeys((*own, *self.projects)))


@dataclass(frozen=True, kw_only=True)
class StoredJiraWrites:
    """What an admin saved beyond the master switch. A kind left out was never set."""

    kinds: Mapping[JiraWriteKind, bool] = field(default_factory=dict)
    create_projects: CreateProjects | None = None


@dataclass(frozen=True, kw_only=True)
class Switch:
    on: bool
    source: SettingSource


@dataclass(frozen=True, kw_only=True)
class JiraWrites:
    """Every switch as it is in force now, each with where its value comes from."""

    master: Switch
    kinds: Mapping[JiraWriteKind, Switch]
    create_projects: CreateProjects
    create_projects_source: SettingSource

    def kind(self, kind: JiraWriteKind) -> Switch:
        return self.kinds.get(kind) or Switch(on=KIND_DEFAULTS[kind], source=SettingSource.DEFAULT)

    def allows(self, kind: JiraWriteKind) -> bool:
        """The master and this kind's switch are both on."""
        return self.master.on and self.kind(kind).on

    def refusal(self, kind: JiraWriteKind) -> str | None:
        """Why this kind may not write now, in a sentence; None while it may."""
        if not self.master.on:
            return MASTER_OFF
        if not self.kind(kind).on:
            return KIND_OFF[kind]
        return None

    def create_refusal(self, project_key: str, own_keys: Collection[str]) -> str | None:
        """Why release readiness may not create this draft in Jira now; None while it may."""
        off = self.refusal(JiraWriteKind.READINESS_CREATE)
        if off is not None:
            return off
        if self.create_projects.allows(project_key, own_keys):
            return None
        return project_refusal(project_key, self.create_projects.allowed_keys(sorted(own_keys)))


def project_refusal(project_key: str, allowed: Sequence[str]) -> str:
    key = project_key.strip().upper() or "with no key"
    if not allowed:
        return f"OpenProgram may not create issues in Jira project {key}."
    return (
        f"This draft is for Jira project {key}, where OpenProgram may not create issues. "
        f"It may create them in {_and_list(allowed)}."
    )


def resolve_jira_writes(
    *,
    master_override: bool | None,
    env_master: bool,
    env_master_set: bool,
    stored: StoredJiraWrites | None,
    legacy_readiness_create: bool | None = None,
) -> JiraWrites:
    """The switches in force from what is stored, the environment and the defaults.

    ``master_override``: the tenant's own master value, None while never set.
    ``env_master_set``: the deployment set ``env_master`` itself (not its default).
    ``legacy_readiness_create``: the release readiness settings' ``create_in_jira``
    when an admin saved them, for a tenant whose admin has not set the kind here.
    """
    if master_override is not None:
        master = Switch(on=master_override, source=SettingSource.ADMIN)
    else:
        master = Switch(
            on=env_master,
            source=SettingSource.ENV if env_master_set else SettingSource.DEFAULT,
        )
    saved = stored or StoredJiraWrites()
    kinds: dict[JiraWriteKind, Switch] = {}
    for kind in KIND_ORDER:
        value = saved.kinds.get(kind)
        if value is None and kind is JiraWriteKind.READINESS_CREATE:
            value = legacy_readiness_create
        kinds[kind] = (
            Switch(on=KIND_DEFAULTS[kind], source=SettingSource.DEFAULT)
            if value is None
            else Switch(on=value, source=SettingSource.ADMIN)
        )
    return JiraWrites(
        master=master,
        kinds=MappingProxyType(kinds),
        create_projects=saved.create_projects or CreateProjects(),
        create_projects_source=(
            SettingSource.DEFAULT if saved.create_projects is None else SettingSource.ADMIN
        ),
    )


@dataclass(frozen=True, kw_only=True)
class JiraWritesUpdate:
    """An admin's change: only what is given changes; the rest keeps its value and source."""

    master: bool | None = None
    kinds: Mapping[JiraWriteKind, bool] = field(default_factory=dict)
    create_projects: CreateProjects | None = None

    @property
    def empty(self) -> bool:
        return self.master is None and not self.kinds and self.create_projects is None


def validated_create_projects(own_project: bool, projects: Iterable[str]) -> CreateProjects:
    """Tidy project keys (capitals, no repeats), or JiraWritesError in a sentence."""
    keys = tuple(dict.fromkeys(key.strip().upper() for key in projects if key.strip()))
    bad = [key for key in keys if not _PROJECT_KEY.match(key)]
    if bad:
        raise JiraWritesError(
            f"A Jira project key is capital letters and digits, such as CHK: {', '.join(bad)}."
        )
    if len(keys) > MAX_CREATE_PROJECTS:
        raise JiraWritesError(f"List at most {MAX_CREATE_PROJECTS} other projects.")
    if not own_project and not keys:
        raise JiraWritesError(
            "Allow at least one project: the scope's own, or a project key. "
            "To stop creating issues, turn off Create release-readiness issues."
        )
    return CreateProjects(own_project=own_project, projects=keys)


def apply_update(stored: StoredJiraWrites | None, update: JiraWritesUpdate) -> StoredJiraWrites:
    """What is stored after an update: given kinds and projects replace, the rest stays."""
    saved = stored or StoredJiraWrites()
    return StoredJiraWrites(
        kinds={**saved.kinds, **update.kinds},
        create_projects=(
            update.create_projects if update.create_projects is not None else saved.create_projects
        ),
    )


# ---- the change log ---------------------------------------------------------------------

#: A switch's value, or the project allow-list's.
SettingValue = bool | CreateProjects


@dataclass(frozen=True, kw_only=True)
class JiraWritesChange:
    """One setting an admin changed: who, when, and its value before and after."""

    tenant_id: str
    at: datetime
    actor: str
    #: ``master``, a kind's value, or ``create_projects``.
    setting: str
    before: SettingValue
    after: SettingValue
    #: Where the value before came from: a default, the environment, or an admin.
    before_source: SettingSource


def changes_between(
    before: JiraWrites, after: JiraWrites, *, tenant_id: str, at: datetime, actor: str
) -> list[JiraWritesChange]:
    """A row for every setting whose value or source the update changed."""
    pairs: list[tuple[str, SettingValue, SettingSource, SettingValue, SettingSource]] = [
        (MASTER, before.master.on, before.master.source, after.master.on, after.master.source)
    ]
    for kind in KIND_ORDER:
        old, new = before.kind(kind), after.kind(kind)
        pairs.append((kind.value, old.on, old.source, new.on, new.source))
    pairs.append(
        (
            CREATE_PROJECTS,
            before.create_projects,
            before.create_projects_source,
            after.create_projects,
            after.create_projects_source,
        )
    )
    return [
        JiraWritesChange(
            tenant_id=tenant_id,
            at=at,
            actor=actor,
            setting=setting,
            before=old_value,
            after=new_value,
            before_source=old_source,
        )
        for setting, old_value, old_source, new_value, new_source in pairs
        if old_value != new_value or old_source is not new_source
    ]


def setting_value_json(value: SettingValue) -> object:
    if isinstance(value, CreateProjects):
        return {"own_project": value.own_project, "projects": list(value.projects)}
    return value


def setting_value_from_json(value: object) -> SettingValue:
    if isinstance(value, Mapping):
        projects = value.get("projects")
        return CreateProjects(
            own_project=bool(value.get("own_project", True)),
            projects=tuple(
                str(key) for key in (projects if isinstance(projects, list) else []) if key
            ),
        )
    return bool(value)


def stored_json(stored: StoredJiraWrites) -> dict[str, object]:
    """The stored shape: a key only for what an admin set, and no nulls."""
    out: dict[str, object] = {kind.value: value for kind, value in stored.kinds.items()}
    if stored.create_projects is not None:
        out[CREATE_PROJECTS] = setting_value_json(stored.create_projects)
    return out


def stored_from_json(value: object) -> StoredJiraWrites | None:
    if not isinstance(value, Mapping):
        return None
    kinds: dict[JiraWriteKind, bool] = {}
    for kind in KIND_ORDER:
        item = value.get(kind.value)
        if isinstance(item, bool):
            kinds[kind] = item
    projects = value.get(CREATE_PROJECTS)
    create = setting_value_from_json(projects) if isinstance(projects, Mapping) else None
    return StoredJiraWrites(
        kinds=kinds, create_projects=create if isinstance(create, CreateProjects) else None
    )


def _and_list(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


__all__ = [
    "CREATE_PROJECTS",
    "KIND_DEFAULTS",
    "KIND_OFF",
    "KIND_ORDER",
    "MASTER",
    "MASTER_OFF",
    "CreateProjects",
    "JiraWriteKind",
    "JiraWrites",
    "JiraWritesChange",
    "JiraWritesError",
    "JiraWritesUpdate",
    "SettingSource",
    "SettingValue",
    "StoredJiraWrites",
    "Switch",
    "apply_update",
    "changes_between",
    "project_refusal",
    "resolve_jira_writes",
    "setting_value_from_json",
    "setting_value_json",
    "stored_from_json",
    "stored_json",
    "validated_create_projects",
]
