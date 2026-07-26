from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from core.domain.conversation import ConversationTurn
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent
from core.domain.identity import IdentityLink
from core.domain.inbound import InboundChatEvent
from core.domain.integrations import (
    BuildResult,
    CalendarEvent,
    Commit,
    Issue,
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
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    DeveloperStatus,
)
from core.domain.writeback import WriteBackAudit
from core.ports.directory import DirectoryUserRepository
from core.ports.repositories import InboundChatEventRepository, TimeSeriesRepository
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
    transitions: list[tuple[str, str, str]] = field(default_factory=list)
    comments: list[tuple[str, str, str]] = field(default_factory=list)

    async def list_projects(self, tenant_id: str) -> list[Project]:
        return [project for project in self.projects if project.tenant_id == tenant_id]

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        return await self.list_issues_for_query(tenant_id, f"project = {project_key}", cursor)

    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]:
        project_key = _project_key_from_jql(jql)
        return [
            issue
            for issue in self.issues.values()
            if issue.tenant_id == tenant_id
            and (project_key is None or _issue_matches_project(issue, project_key))
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
        self.transitions.append((tenant_id, key, to_state))

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        self.comments.append((tenant_id, key, body))


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
    checkin_clarifications: dict[tuple[str, str, int], CheckInClarification] = field(
        default_factory=dict
    )
    developer_statuses: list[DeveloperStatus] = field(default_factory=list)
    developer_ids: set[str] = field(default_factory=set)

    async def record_checkin(self, checkin: CheckIn) -> None:
        stored = _checkin_with_last_accessed_at(checkin)
        self.developer_ids.add(checkin.developer_id)
        self.checkins = [
            existing
            for existing in self.checkins
            if not (
                existing.tenant_id == stored.tenant_id
                and existing.correlation_id == stored.correlation_id
            )
        ]
        self.checkins.append(stored)

    async def record_checkin_reply_once(self, checkin: CheckIn) -> bool:
        existing = await self.checkin_by_correlation(checkin.tenant_id, checkin.correlation_id)
        if existing is not None and existing.replied_at is not None:
            return False
        await self.record_checkin(checkin)
        return True

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        for index in range(len(self.checkins) - 1, -1, -1):
            checkin = self.checkins[index]
            if checkin.tenant_id == tenant_id and checkin.correlation_id == correlation_id:
                if checkin.raw_reply is not None:
                    checkin = replace(checkin, last_accessed_at=datetime.now(tz=UTC))
                    self.checkins[index] = checkin
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

    async def unconsumed_checkin_correlations_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        matching = [
            correlation
            for correlation in self.checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_thread_ref == chat_thread_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return sorted(matching, key=lambda correlation: correlation.asked_at, reverse=True)

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = await self.unconsumed_checkin_correlations_for_user(
            tenant_id,
            chat_user_ref,
            as_of,
        )
        return matching[0] if matching else None

    async def unconsumed_checkin_correlations_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        matching = [
            correlation
            for correlation in self.checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_user_ref == chat_user_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return sorted(matching, key=lambda correlation: correlation.asked_at, reverse=True)

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

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]:
        return sorted(
            (
                preference
                for (preference_tenant_id, _), preference in self.checkin_preferences.items()
                if preference_tenant_id == tenant_id
            ),
            key=lambda preference: preference.developer_id,
        )

    async def delete_checkin_preference(self, tenant_id: str, developer_id: str) -> None:
        self.checkin_preferences.pop((tenant_id, developer_id), None)

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.checkin_schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self.checkin_schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def checkin_schedule_run_for_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInScheduleRun | None:
        for run in self.checkin_schedule_runs.values():
            if run.tenant_id == tenant_id and run.correlation_id == correlation_id:
                return run
        return None

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

    async def record_checkin_clarification(
        self, clarification: CheckInClarification
    ) -> CheckInClarification:
        key = (
            clarification.tenant_id,
            clarification.correlation_id,
            clarification.clarification_number,
        )
        existing = self.checkin_clarifications.get(key)
        if existing is not None:
            if existing.outbound_message_id is not None:
                return existing
            updated = CheckInClarification(
                tenant_id=existing.tenant_id,
                correlation_id=existing.correlation_id,
                clarification_number=existing.clarification_number,
                question=existing.question or clarification.question,
                sent_at=clarification.sent_at or existing.sent_at,
                outbound_message_id=(
                    clarification.outbound_message_id or existing.outbound_message_id
                ),
            )
            self.checkin_clarifications[key] = updated
            return updated
        self.checkin_clarifications[key] = clarification
        return clarification

    async def checkin_clarification_count(self, tenant_id: str, correlation_id: str) -> int:
        return sum(
            1
            for existing_tenant_id, existing_correlation_id, _ in self.checkin_clarifications
            if existing_tenant_id == tenant_id and existing_correlation_id == correlation_id
        )

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

    async def purge_checkin_raw_replies_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        cleared = 0
        retained: list[CheckIn] = []
        for checkin in self.checkins:
            if (
                checkin.tenant_id == tenant_id
                and checkin.raw_reply is not None
                and _checkin_last_accessed_at(checkin) < cutoff
            ):
                retained.append(replace(checkin, raw_reply=None))
                cleared += 1
                continue
            retained.append(checkin)
        self.checkins = retained
        return cleared


@dataclass
class FakeIdentityLinkRepository:
    identity_links: dict[tuple[str, str], IdentityLink] = field(default_factory=dict)

    async def get_identity_link(self, tenant_id: str, developer_id: str) -> IdentityLink | None:
        return self.identity_links.get((tenant_id, developer_id))

    async def upsert_identity_link(self, link: IdentityLink) -> None:
        self.identity_links[(link.tenant_id, link.developer_id)] = link

    async def list_identity_links(self, tenant_id: str) -> list[IdentityLink]:
        return sorted(
            (
                link
                for (link_tenant_id, _), link in self.identity_links.items()
                if link_tenant_id == tenant_id
            ),
            key=lambda link: link.developer_id,
        )


@dataclass
class FakeDirectoryUserRepository(DirectoryUserRepository):
    users: dict[tuple[str, str], DirectoryUser] = field(default_factory=dict)

    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None:
        for user in users:
            self.users[(user.tenant_id, user.external_id)] = user

    async def search(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[DirectoryUser]:
        query_value = query.strip().lower()
        filtered = sorted(
            (
                user
                for (user_tenant, _), user in self.users.items()
                if user_tenant == tenant_id
                and user.is_active
                and (
                    not query_value
                    or query_value in user.display_name.lower()
                    or (user.email is not None and query_value in user.email.lower())
                    or (user.handle is not None and query_value in user.handle.lower())
                    or query_value in user.external_id.lower()
                )
            ),
            key=lambda user: (user.display_name.lower(), user.external_id),
        )
        return filtered[offset : offset + limit]

    async def count(self, tenant_id: str, query: str = "") -> int:
        query_value = query.strip().lower()
        return sum(
            1
            for (user_tenant, _), user in self.users.items()
            if user_tenant == tenant_id
            and user.is_active
            and (
                not query_value
                or query_value in user.display_name.lower()
                or (user.email is not None and query_value in user.email.lower())
                or (user.handle is not None and query_value in user.handle.lower())
                or query_value in user.external_id.lower()
            )
        )

    async def get(self, tenant_id: str, external_id: str) -> DirectoryUser | None:
        return self.users.get((tenant_id, external_id))

    async def deactivate_missing(self, tenant_id: str, seen_external_ids: Sequence[str]) -> int:
        seen = set(seen_external_ids)
        updated = 0
        for key, user in list(self.users.items()):
            if key[0] != tenant_id or not user.is_active or user.external_id in seen:
                continue
            self.users[key] = DirectoryUser(
                tenant_id=user.tenant_id,
                external_id=user.external_id,
                display_name=user.display_name,
                email=user.email,
                handle=user.handle,
                avatar_url=user.avatar_url,
                title=user.title,
                is_active=False,
                source=user.source,
                synced_at=user.synced_at,
                metadata=dict(user.metadata),
            )
            updated += 1
        return updated


@dataclass
class FakeTimeSeriesRepository(TimeSeriesRepository):
    facts: list[FactEvent] = field(default_factory=list)

    async def append_fact(self, fact: FactEvent) -> None:
        if _fact_identity(fact) in {_fact_identity(existing) for existing in self.facts}:
            return
        self.facts.append(fact)

    async def append_fact_once(self, fact: FactEvent) -> None:
        await self.append_fact(fact)

    async def list_facts(
        self,
        tenant_id: str,
        entity_ref: EntityRef,
        since: datetime | None = None,
    ) -> list[FactEvent]:
        return [
            fact
            for fact in sorted(self.facts, key=_fact_sort_key)
            if fact.tenant_id == tenant_id
            and fact.entity_ref == entity_ref
            and (since is None or fact.observed_at >= since)
        ]

    async def list_recent_facts(
        self,
        tenant_id: str,
        since: datetime | None = None,
        sources: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactEvent]:
        if limit <= 0:
            return []
        source_filter = set(sources) if sources is not None else None
        matching = [
            fact
            for fact in sorted(self.facts, key=_fact_sort_key, reverse=True)
            if fact.tenant_id == tenant_id
            and (since is None or fact.observed_at >= since)
            and (source_filter is None or fact.source in source_filter)
        ]
        return matching[:limit]


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
class FakeConversationRepository:
    turns: list[ConversationTurn] = field(default_factory=list)

    async def append_turn(self, turn: ConversationTurn) -> None:
        self.turns.append(
            turn
            if turn.last_accessed_at is not None
            else replace(turn, last_accessed_at=turn.observed_at)
        )

    async def list_turns_for_day(
        self, tenant_id: str, developer_id: str, on: date
    ) -> list[ConversationTurn]:
        matched = sorted(
            (
                turn
                for turn in self.turns
                if turn.tenant_id == tenant_id
                and turn.developer_id == developer_id
                and turn.conversation_date == on
            ),
            key=_conversation_sort_key,
        )
        self._touch_turns(matched)
        return matched

    async def list_recent_turns(
        self,
        tenant_id: str,
        developer_id: str,
        limit: int,
        since: datetime | None = None,
    ) -> list[ConversationTurn]:
        if limit <= 0:
            return []
        matching = sorted(
            (
                turn
                for turn in self.turns
                if turn.tenant_id == tenant_id
                and turn.developer_id == developer_id
                and (since is None or turn.observed_at >= since)
            ),
            key=_conversation_sort_key,
            reverse=True,
        )
        matched = sorted(matching[:limit], key=_conversation_sort_key)
        self._touch_turns(matched)
        return matched

    async def user_turn_exists(
        self, tenant_id: str, developer_id: str, chat_message_id: str
    ) -> bool:
        return any(
            turn.tenant_id == tenant_id
            and turn.developer_id == developer_id
            and turn.chat_message_id == chat_message_id
            and turn.role.value == "user"
            for turn in self.turns
        )

    async def purge_turns_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        retained = [
            turn
            for turn in self.turns
            if turn.tenant_id != tenant_id or _turn_last_accessed_at(turn) >= cutoff
        ]
        deleted_count = len(self.turns) - len(retained)
        self.turns = retained
        return deleted_count

    def _touch_turns(self, turns: Sequence[ConversationTurn]) -> None:
        if not turns:
            return
        touched_at = datetime.now(tz=UTC)
        turn_keys = {_conversation_identity(turn) for turn in turns}
        self.turns = [
            replace(turn, last_accessed_at=touched_at)
            if _conversation_identity(turn) in turn_keys
            else turn
            for turn in self.turns
        ]


@dataclass
class FakeInboundChatEventRepository(InboundChatEventRepository):
    events: list[InboundChatEvent] = field(default_factory=list)
    _counter: int = 0

    async def append(self, event: InboundChatEvent) -> bool:
        for existing in self.events:
            if (
                existing.tenant_id == event.tenant_id
                and existing.provider == event.provider
                and existing.event_id == event.event_id
            ):
                return False
        self._counter += 1
        self.events.append(
            event if event.id is not None else replace(event, id=f"evt-{self._counter}")
        )
        return True

    async def list_unprocessed_for_conversation(
        self, tenant_id: str, conversation_key: str
    ) -> list[InboundChatEvent]:
        return sorted(
            (
                event
                for event in self.events
                if event.tenant_id == tenant_id
                and event.conversation_key == conversation_key
                and event.processed_at is None
            ),
            key=lambda event: (event.received_at, event.message_ref),
        )

    async def mark_processed(
        self, tenant_id: str, event_ids: Sequence[str], processed_at: datetime
    ) -> None:
        ids = set(event_ids)
        self.events = [
            replace(event, processed_at=processed_at)
            if event.tenant_id == tenant_id and event.id in ids and event.processed_at is None
            else event
            for event in self.events
        ]

    async def list_stuck(self, tenant_id: str, older_than: datetime) -> list[InboundChatEvent]:
        return sorted(
            (
                event
                for event in self.events
                if event.tenant_id == tenant_id
                and event.processed_at is None
                and event.received_at < older_than
            ),
            key=lambda event: (event.received_at, event.message_ref),
        )

    async def purge_processed_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        retained = [
            event
            for event in self.events
            if event.tenant_id != tenant_id
            or event.processed_at is None
            or event.processed_at >= cutoff
        ]
        deleted_count = len(self.events) - len(retained)
        self.events = retained
        return deleted_count


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


def _project_key_from_jql(jql: str) -> str | None:
    normalized = jql.replace("'", '"')
    marker = "project ="
    index = normalized.lower().find(marker)
    if index < 0:
        return None
    value = normalized[index + len(marker) :].strip()
    if value.startswith('"'):
        return value.split('"', maxsplit=2)[1] if value.count('"') >= 2 else None
    return value.split(maxsplit=1)[0].strip("()") or None


def _pull_request_matches_repo(pull_request: PullRequest, repo: str) -> bool:
    repo_name = pull_request.metadata.get("repo")
    return repo_name is None or repo_name == repo


def _is_after_cursor(updated_at: datetime | None, cursor: SyncCursor) -> bool:
    return cursor.updated_at is None or updated_at is None or updated_at > cursor.updated_at


def _conversation_sort_key(
    turn: ConversationTurn,
) -> tuple[datetime, str, str, str, str, str]:
    return (
        turn.observed_at,
        turn.conversation_id,
        turn.correlation_id or "",
        turn.chat_message_id or "",
        turn.role.value,
        turn.content,
    )


def _conversation_identity(
    turn: ConversationTurn,
) -> tuple[str, str, str, date, str, str | None, str | None, datetime]:
    return (
        turn.tenant_id,
        turn.developer_id,
        turn.conversation_id,
        turn.conversation_date,
        turn.role.value,
        turn.correlation_id,
        turn.chat_message_id,
        turn.observed_at,
    )


def _turn_last_accessed_at(turn: ConversationTurn) -> datetime:
    return turn.last_accessed_at or turn.observed_at


def _checkin_with_last_accessed_at(checkin: CheckIn) -> CheckIn:
    if checkin.last_accessed_at is not None:
        return checkin
    return replace(checkin, last_accessed_at=_checkin_last_accessed_at(checkin))


def _checkin_last_accessed_at(checkin: CheckIn) -> datetime:
    return checkin.last_accessed_at or checkin.replied_at or checkin.asked_at


def _fact_sort_key(fact: FactEvent) -> tuple[datetime, datetime, str]:
    return (fact.observed_at, fact.ingested_at, fact.correlation_id)


def _fact_identity(fact: FactEvent) -> tuple[str, str, str, str, str, datetime]:
    return (
        fact.tenant_id,
        fact.source,
        fact.entity_ref.kind.value,
        fact.entity_ref.id,
        fact.correlation_id,
        fact.observed_at,
    )


@dataclass
class FakeWriteBackConfigRepository:
    enabled: dict[str, bool] = field(default_factory=dict)

    async def get_writeback_enabled(self, tenant_id: str) -> bool | None:
        return self.enabled.get(tenant_id)

    async def set_writeback_enabled(self, tenant_id: str, enabled: bool) -> None:
        self.enabled[tenant_id] = enabled


@dataclass
class FakeWriteBackAuditRepository:
    audits: dict[str, WriteBackAudit] = field(default_factory=dict)

    async def record(self, audit: WriteBackAudit) -> None:
        self.audits.setdefault(audit.id, audit)

    async def list_for_issue(self, tenant_id: str, issue_key: str) -> list[WriteBackAudit]:
        return sorted(
            (
                audit
                for audit in self.audits.values()
                if audit.tenant_id == tenant_id and audit.issue_key == issue_key
            ),
            key=lambda audit: audit.created_at,
        )

    async def find_existing(
        self,
        tenant_id: str,
        issue_key: str,
        target_state: str,
        correlation_id: str,
    ) -> WriteBackAudit | None:
        matches = [
            audit
            for audit in self.audits.values()
            if audit.tenant_id == tenant_id
            and audit.issue_key == issue_key
            and audit.target_state == target_state
            and audit.correlation_id == correlation_id
        ]
        if not matches:
            return None
        return min(matches, key=lambda audit: audit.created_at)
