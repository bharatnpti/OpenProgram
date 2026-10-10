"""Request and response shapes of Admin › Jira writes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from core.domain.jira_writes import (
    KIND_ORDER,
    MAX_CREATE_PROJECTS,
    CreateProjects,
    JiraWriteKind,
    JiraWrites,
    JiraWritesChange,
    JiraWritesUpdate,
    SettingSource,
    validated_create_projects,
)


class JiraWriteSwitchDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: bool
    source: SettingSource


class JiraWriteKindDto(BaseModel):
    """One kind of Jira write: its own switch, and whether it writes now."""

    model_config = ConfigDict(frozen=True)

    kind: JiraWriteKind
    #: This kind's own switch, read only while the master switch is on.
    on: bool
    source: SettingSource
    #: It writes now: the master switch and this one are both on.
    effective: bool


class JiraCreateProjectsDto(BaseModel):
    """The Jira projects release readiness may create issues in."""

    model_config = ConfigDict(frozen=True)

    #: The project of the scope the draft is for (a pod's: the projects it works on).
    own_project: bool
    #: Other project keys, allowed as well.
    projects: list[str]
    source: SettingSource

    @classmethod
    def from_domain(cls, value: CreateProjects, source: SettingSource) -> JiraCreateProjectsDto:
        return cls(own_project=value.own_project, projects=list(value.projects), source=source)


class JiraCreateProjectsValueDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    own_project: bool
    projects: list[str]


class JiraWritesChangeDto(BaseModel):
    """One change an admin made: who, when, and the value before and after."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    by: str
    by_name: str | None
    #: ``master``, a kind (``checkin_updates`` …), or ``create_projects``.
    setting: str
    #: A switch's value before and after; None for ``create_projects``.
    before_on: bool | None
    after_on: bool | None
    #: The allowed projects before and after; None for a switch.
    before_projects: JiraCreateProjectsValueDto | None
    after_projects: JiraCreateProjectsValueDto | None
    #: Where the value before came from.
    before_source: SettingSource

    @classmethod
    def from_domain(cls, change: JiraWritesChange, names: Mapping[str, str]) -> JiraWritesChangeDto:
        def on(value: object) -> bool | None:
            return value if isinstance(value, bool) else None

        def projects(value: object) -> JiraCreateProjectsValueDto | None:
            if not isinstance(value, CreateProjects):
                return None
            return JiraCreateProjectsValueDto(
                own_project=value.own_project, projects=list(value.projects)
            )

        return cls(
            at=change.at,
            by=change.actor,
            by_name=names.get(change.actor),
            setting=change.setting,
            before_on=on(change.before),
            after_on=on(change.after),
            before_projects=projects(change.before),
            after_projects=projects(change.after),
            before_source=change.before_source,
        )


class JiraWritesResponse(BaseModel):
    """The tenant's Jira write switches as they are in force, and the latest changes."""

    model_config = ConfigDict(frozen=True)

    #: "Jira writes for this tenant": off, nothing writes to Jira, whatever else is on.
    master: JiraWriteSwitchDto
    kinds: list[JiraWriteKindDto]
    create_projects: JiraCreateProjectsDto
    #: Newest first.
    changes: list[JiraWritesChangeDto]

    @classmethod
    def from_domain(
        cls,
        writes: JiraWrites,
        changes: list[JiraWritesChange],
        names: Mapping[str, str],
    ) -> JiraWritesResponse:
        return cls(
            master=JiraWriteSwitchDto(on=writes.master.on, source=writes.master.source),
            kinds=[
                JiraWriteKindDto(
                    kind=kind,
                    on=writes.kind(kind).on,
                    source=writes.kind(kind).source,
                    effective=writes.allows(kind),
                )
                for kind in KIND_ORDER
            ],
            create_projects=JiraCreateProjectsDto.from_domain(
                writes.create_projects, writes.create_projects_source
            ),
            changes=[JiraWritesChangeDto.from_domain(change, names) for change in changes],
        )


ProjectKey = Annotated[str, Field(min_length=1, max_length=40)]


class JiraCreateProjectsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    own_project: bool
    projects: list[ProjectKey] = Field(default_factory=list, max_length=MAX_CREATE_PROJECTS)


class JiraWritesUpdateRequest(BaseModel):
    """Only what is given changes; a field left out or null keeps its value and source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    master: bool | None = None
    checkin_updates: bool | None = None
    console_moves: bool | None = None
    readiness_create: bool | None = None
    create_projects: JiraCreateProjectsUpdate | None = None

    def to_domain(self) -> JiraWritesUpdate:
        """The update, its project keys tidied; JiraWritesError for keys that cannot be saved."""
        given = {
            JiraWriteKind.CHECKIN_UPDATES: self.checkin_updates,
            JiraWriteKind.CONSOLE_MOVES: self.console_moves,
            JiraWriteKind.READINESS_CREATE: self.readiness_create,
        }
        return JiraWritesUpdate(
            master=self.master,
            kinds={kind: on for kind, on in given.items() if on is not None},
            create_projects=(
                validated_create_projects(
                    self.create_projects.own_project, self.create_projects.projects
                )
                if self.create_projects is not None
                else None
            ),
        )
