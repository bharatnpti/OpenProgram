from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from core.domain.graph import EntityRef
from core.domain.integrations import (
    BuildResult,
    CalendarEvent,
    Commit,
    Issue,
    IssueState,
    Project,
    PullRequest,
    Repo,
    Sprint,
    SyncCursor,
    UserRef,
)
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.rollup import NodeStatus
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    DeveloperStatus,
)
from infra.adapters.llm.fake import FakeLlmProvider

__all__ = ["FakeLlmProvider"]


@dataclass
class FakeChatProvider:
    sent: list[OutboundMessage] = field(default_factory=list)
    replies: dict[str, InboundMessage] = field(default_factory=dict)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"msg-{user.external_id}-{len(self.sent)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return self.replies.get(thread_id)


@dataclass
class FakeIssueTracker:
    issues: dict[str, Issue] = field(default_factory=dict)
    projects: list[Project] = field(default_factory=list)
    sprints: list[Sprint] = field(default_factory=list)

    async def list_projects(self, tenant_id: str) -> list[Project]:
        return [project for project in self.projects if project.tenant_id == tenant_id]

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        return [
            issue
            for issue in self.issues.values()
            if issue.tenant_id == tenant_id
            and _issue_matches_project(issue, project_key)
            and _is_after_cursor(issue.updated_at, cursor)
        ]

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]:
        return [
            sprint
            for sprint in self.sprints
            if sprint.tenant_id == tenant_id and sprint.board_id == board_id
        ]

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        return self.issues[key]

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        return [issue for issue in self.issues.values() if issue.assignee == assignee]

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        issue = self.issues[key]
        self.issues[key] = Issue(
            tenant_id=issue.tenant_id,
            key=issue.key,
            title=issue.title,
            state=IssueState(to_state),
            assignee=issue.assignee,
            metadata=issue.metadata,
            updated_at=issue.updated_at,
        )

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        issue = self.issues[key]
        self.issues[key] = Issue(
            tenant_id=issue.tenant_id,
            key=issue.key,
            title=issue.title,
            state=issue.state,
            assignee=issue.assignee,
            metadata={**issue.metadata, "last_comment": body},
            updated_at=issue.updated_at,
        )


@dataclass
class FakeVcsProvider:
    repos: list[Repo] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    pull_requests: list[PullRequest] = field(default_factory=list)

    async def list_repos(self, tenant_id: str) -> list[Repo]:
        return [repo for repo in self.repos if repo.tenant_id == tenant_id]

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]:
        return [
            commit
            for commit in self.commits
            if commit.tenant_id == tenant_id
            and commit.repo == repo
            and _is_after_cursor(commit.committed_at, cursor)
        ]

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]:
        return [
            pull_request
            for pull_request in self.pull_requests
            if pull_request.tenant_id == tenant_id
            and _pull_request_matches_repo(pull_request, repo)
            and (cursor is None or _is_after_cursor(pull_request.updated_at, cursor))
        ]

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        return [
            pull_request for pull_request in self.pull_requests if pull_request.author == author
        ]


@dataclass
class FakeStatusRepository:
    checkins: list[CheckIn] = field(default_factory=list)
    checkin_correlations: list[CheckInCorrelation] = field(default_factory=list)
    checkin_preferences: dict[tuple[str, str], CheckInPreference] = field(default_factory=dict)
    checkin_schedule_runs: dict[tuple[str, str, date], CheckInScheduleRun] = field(
        default_factory=dict
    )
    checkin_nudges: dict[tuple[str, str, int], CheckInNudge] = field(default_factory=dict)
    developer_statuses: list[DeveloperStatus] = field(default_factory=list)
    developer_ids: set[str] = field(default_factory=set)

    async def record_checkin(self, checkin: CheckIn) -> None:
        self.developer_ids.add(checkin.developer_id)
        self.checkins = [
            existing
            for existing in self.checkins
            if not (
                existing.tenant_id == checkin.tenant_id
                and existing.correlation_id == checkin.correlation_id
            )
        ]
        self.checkins.append(checkin)

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        for checkin in reversed(self.checkins):
            if checkin.tenant_id == tenant_id and checkin.correlation_id == correlation_id:
                return checkin
        return None

    async def record_checkin_correlation(self, correlation: CheckInCorrelation) -> None:
        self.checkin_correlations = [
            existing
            for existing in self.checkin_correlations
            if not (
                existing.tenant_id == correlation.tenant_id
                and existing.correlation_id == correlation.correlation_id
            )
        ]
        self.checkin_correlations.append(correlation)

    async def checkin_correlation_by_id(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInCorrelation | None:
        for correlation in reversed(self.checkin_correlations):
            if correlation.tenant_id == tenant_id and correlation.correlation_id == correlation_id:
                return correlation
        return None

    async def latest_checkin_correlation_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = [
            correlation
            for correlation in self.checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_thread_ref == chat_thread_ref
            and correlation.asked_at.date() == as_of
        ]
        return max(matching, key=lambda correlation: correlation.asked_at) if matching else None

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = [
            correlation
            for correlation in self.checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_user_ref == chat_user_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return max(matching, key=lambda correlation: correlation.asked_at) if matching else None

    async def consume_checkin_correlation(
        self, tenant_id: str, correlation_id: str, consumed_at: datetime
    ) -> None:
        correlation = await self.checkin_correlation_by_id(tenant_id, correlation_id)
        if correlation is None or correlation.consumed_at is not None:
            return
        await self.record_checkin_correlation(
            CheckInCorrelation(
                tenant_id=correlation.tenant_id,
                correlation_id=correlation.correlation_id,
                developer_id=correlation.developer_id,
                chat_user_ref=correlation.chat_user_ref,
                chat_thread_ref=correlation.chat_thread_ref,
                outbound_message_id=correlation.outbound_message_id,
                asked_at=correlation.asked_at,
                consumed_at=consumed_at,
            )
        )

    async def record_checkin_preference(self, preference: CheckInPreference) -> None:
        self.checkin_preferences[(preference.tenant_id, preference.developer_id)] = preference

    async def checkin_preference_for(
        self, tenant_id: str, developer_id: str
    ) -> CheckInPreference | None:
        return self.checkin_preferences.get((tenant_id, developer_id))

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.checkin_schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self.checkin_schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def record_checkin_nudge(self, nudge: CheckInNudge) -> CheckInNudge:
        key = (nudge.tenant_id, nudge.correlation_id, nudge.nudge_number)
        existing = self.checkin_nudges.get(key)
        if existing is not None:
            if existing.outbound_message_id is not None:
                return existing
            updated = CheckInNudge(
                tenant_id=existing.tenant_id,
                correlation_id=existing.correlation_id,
                nudge_number=existing.nudge_number,
                sent_at=nudge.sent_at or existing.sent_at,
                outbound_message_id=nudge.outbound_message_id or existing.outbound_message_id,
            )
            self.checkin_nudges[key] = updated
            return updated
        self.checkin_nudges[key] = nudge
        return nudge

    async def checkin_nudge_for(
        self, tenant_id: str, correlation_id: str, nudge_number: int
    ) -> CheckInNudge | None:
        return self.checkin_nudges.get((tenant_id, correlation_id, nudge_number))

    async def record_developer_status(self, status: DeveloperStatus) -> None:
        self.developer_ids.add(status.developer_id)
        self.developer_statuses.append(status)

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        matching = [
            status
            for status in self.developer_statuses
            if status.tenant_id == tenant_id
            and status.developer_id == developer_id
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]:
        known_developer_ids = {
            *self.developer_ids,
            *(checkin.developer_id for checkin in self.checkins if checkin.tenant_id == tenant_id),
            *(
                status.developer_id
                for status in self.developer_statuses
                if status.tenant_id == tenant_id
            ),
        }
        replied_developer_ids = {
            checkin.developer_id
            for checkin in self.checkins
            if checkin.tenant_id == tenant_id
            and checkin.replied_at is not None
            and checkin.replied_at.date() == as_of
        }
        return sorted(known_developer_ids - replied_developer_ids)


@dataclass
class FakeRollupRepository:
    node_statuses: list[NodeStatus] = field(default_factory=list)

    async def record_node_status(self, status: NodeStatus) -> None:
        self.node_statuses.append(status)

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None:
        matching = [
            status
            for status in self.node_statuses
            if status.entity_ref.tenant_id == tenant_id
            and status.entity_ref == entity_ref
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        return [
            status
            for status in self.node_statuses
            if status.entity_ref.tenant_id == tenant_id and status.as_of <= as_of
        ]


@dataclass
class FakeSyncCursorRepository:
    cursors: dict[tuple[str, str, str], SyncCursor] = field(default_factory=dict)

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        return self.cursors.get((tenant_id, connector, scope), SyncCursor())

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        self.cursors[(tenant_id, connector, scope)] = cursor


@dataclass
class FakeCiProvider:
    builds: list[BuildResult] = field(default_factory=list)

    async def latest_build(self, tenant_id: str, pipeline_id: str) -> BuildResult | None:
        matching = [
            build
            for build in self.builds
            if build.tenant_id == tenant_id and build.id == pipeline_id
        ]
        return matching[-1] if matching else None

    async def list_recent_failures(self, tenant_id: str, repo: str) -> list[BuildResult]:
        return [
            build
            for build in self.builds
            if build.tenant_id == tenant_id and build.status == "failed"
        ]


@dataclass
class FakeCalendarProvider:
    events: list[CalendarEvent] = field(default_factory=list)

    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]:
        return [
            event
            for event in self.events
            if event.user == user and event.starts_on >= start and event.ends_on <= end
        ]


def _issue_matches_project(issue: Issue, project_key: str) -> bool:
    return issue.metadata.get("project_key") == project_key or issue.key.startswith(
        f"{project_key}-"
    )


def _pull_request_matches_repo(pull_request: PullRequest, repo: str) -> bool:
    repo_name = pull_request.metadata.get("repo")
    return repo_name is None or repo_name == repo


def _is_after_cursor(updated_at: datetime | None, cursor: SyncCursor) -> bool:
    return cursor.updated_at is None or updated_at is None or updated_at > cursor.updated_at
