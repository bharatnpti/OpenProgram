"""Seed the local demo tenant with a month of delivery history.

Writes through the real repositories and then runs the real rollup, risk, and
drift services over every seeded day, so the numbers on screen are *derived*
the same way production derives them rather than hand-written. Only the raw
inputs are invented: check-ins and their replies, blocker lifecycles, Jira/Git
facts, and cross-person requests.

    OPENPROGRAM_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/openprogram \\
        PYTHONPATH=backend uv run python -m scripts.seed_demo_history

Re-running is safe: every write is an upsert or an ``append_fact_once``, and
``--reset`` clears the tenant's history tables first.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from config.settings import get_settings
from core.application.blocker_resolution import BlockerResolutionService
from core.application.narrative_brief_service import NarrativeBriefService
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.risk_service import RiskService
from core.application.rollup_service import RollupService
from core.domain.blockers import (
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.brief import BriefKind
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Program,
    WorkItem,
)
from core.domain.graph import (
    Pod as PodNode,
)
from core.domain.graph import (
    Project as ProjectNode,
)
from core.domain.graph import (
    Task as TaskNode,
)
from core.domain.graph import (
    Workstream as WorkstreamNode,
)
from core.domain.identity import IdentityLink
from core.domain.risk import RiskProviderConfig
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    StatusSource,
)
from infra.registry import ServiceRegistry
from scripts import demo_roster as roster

# A calendar month of weekdays ending today.
WINDOW_DAYS = 30
# Risk/drift assessment is the expensive pass; only the recent tail is needed
# for the screens, which all read "open findings as of a day".
RISK_TAIL_DAYS = 12

_PEOPLE_BY_ID = {person.id: person for person in roster.PEOPLE}
_WORKSTREAM_BY_ID = {ws.id: ws for ws in roster.WORKSTREAMS}
_PROJECT_BY_ID = {project.id: project for project in roster.PROJECTS}


@dataclass(frozen=True, kw_only=True)
class SeedContext:
    registry: ServiceRegistry
    tenant_id: str
    today: date
    days: tuple[date, ...]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete this tenant's graph and history first",
    )
    parser.add_argument(
        "--skip-risk",
        action="store_true",
        help="skip the risk/drift assessment pass (much faster)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(reset=args.reset, skip_risk=args.skip_risk))


async def _run(*, reset: bool, skip_risk: bool) -> int:
    settings = get_settings()
    registry = ServiceRegistry(settings)
    today = datetime.now(tz=UTC).date()
    context = SeedContext(
        registry=registry,
        tenant_id=settings.tenant_id,
        today=today,
        days=_weekdays(today, WINDOW_DAYS),
    )
    try:
        if reset:
            await _reset_tenant(context)
            print("reset: cleared tenant history")
        await _seed_directory(context)
        await _seed_graph(context)
        print(f"graph: {len(roster.PEOPLE)} people, {len(roster.WORK_ITEMS)} work items")
        await _seed_delivery_facts(context)
        print("facts: jira + git signals written")
        blockers = await _seed_checkin_history(context)
        print(f"history: {len(context.days)} weekdays of check-ins, {blockers} blocker rows")
        await _seed_cross_person_requests(context)
        print(f"coordination: {len(roster.CROSS_PERSON_REQUESTS)} cross-person requests")
        await _run_rollups(context)
        print(f"rollups: node statuses derived for {len(context.days)} days")
        if skip_risk:
            print("risk: skipped")
        else:
            await _run_risk_and_drift(context)
            print(f"risk: assessed the last {RISK_TAIL_DAYS} days")
        await _seed_briefs(context)
        print("briefs: daily/weekly/exec narratives written")
    finally:
        await registry.close()
    print("\nseed complete.")
    return 0


# --------------------------------------------------------------------------
# reset
# --------------------------------------------------------------------------


_HISTORY_TABLES = (
    "checkin_clarifications",
    "checkin_nudges",
    "checkin_schedule_runs",
    "checkin_correlations",
    "checkins",
    "checkin_preferences",
    "conversation_turns",
    "developer_blockers",
    "developer_statuses",
    "node_statuses",
    "narrative_briefs",
    "cross_person_requests",
    "inbound_chat_events",
    "writeback_audit",
    "facts",
    "graph_edges",
    "graph_nodes",
    "directory_users",
)


async def _reset_tenant(context: SeedContext) -> None:
    executor = context.registry._executor()  # noqa: SLF001 - local seeding utility
    for table in _HISTORY_TABLES:
        await executor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (context.tenant_id,))
    # The chat transcript lives in Redis, not Postgres: without this a re-seed
    # leaves every earlier run's bot questions stacked in each thread.
    if context.registry.chat_simulator_available():
        await context.registry.reset_chat_simulator()


# --------------------------------------------------------------------------
# directory + graph
# --------------------------------------------------------------------------


async def _seed_directory(context: SeedContext) -> None:
    """Pull the mock chat workspace roster into ``directory_users``."""
    await context.registry.directory_sync_service().sync(context.tenant_id)


async def _seed_graph(context: SeedContext) -> None:
    await _seed_hierarchy(context)
    await _seed_people(context)
    await _seed_work_items(context)
    await _seed_tasks(context)


async def _seed_hierarchy(context: SeedContext) -> None:
    """Program, projects, workstreams, pods and the edges between them."""
    graph = context.registry.graph_repository()
    tenant_id = context.tenant_id
    window_start = context.today - timedelta(days=WINDOW_DAYS + 7)

    await graph.upsert_node(
        Program(
            tenant_id=tenant_id,
            id=roster.PROGRAM_ID,
            name=roster.PROGRAM_NAME,
            metadata={"description": "All customer-facing platform delivery"},
        )
    )
    for project in roster.PROJECTS:
        await graph.upsert_node(
            ProjectNode(
                tenant_id=tenant_id,
                id=project.id,
                name=project.name,
                metadata={
                    "code": project.code,
                    "jira_project_key": project.jira_project_key,
                    "github_repos": ",".join(project.repos),
                },
            )
        )
        await _edge(graph, tenant_id, roster.PROGRAM_ID, project.id, valid_from=window_start)

    for workstream in roster.WORKSTREAMS:
        await graph.upsert_node(
            WorkstreamNode(
                tenant_id=tenant_id,
                id=workstream.id,
                name=workstream.name,
                metadata={"github_repos": ",".join(workstream.repos)},
            )
        )
        await _edge(graph, tenant_id, workstream.project_id, workstream.id, valid_from=window_start)

    for pod in roster.PODS:
        await graph.upsert_node(PodNode(tenant_id=tenant_id, id=pod.id, name=pod.name, metadata={}))
        await _edge(graph, tenant_id, pod.project_id, pod.id, valid_from=window_start)
        for workstream_id in pod.workstream_ids:
            # ASSIGNED_TO, matching what `assign_pod_workstream` writes. A
            # CONTAINS edge here is invisible to `pods_for_task`, so a blocker
            # carrying only a work item resolves to no pod and is reported
            # "unattributed" even though the graph does know whose it is.
            await _edge(
                graph,
                tenant_id,
                pod.id,
                workstream_id,
                kind=EdgeKind.ASSIGNED_TO,
                valid_from=window_start,
            )


async def _seed_people(context: SeedContext) -> None:
    """Developer nodes, pod memberships, and per-person check-in timing."""
    graph = context.registry.graph_repository()
    tenant_id = context.tenant_id
    window_start = context.today - timedelta(days=WINDOW_DAYS + 7)

    for person in roster.PEOPLE:
        await graph.upsert_node(
            Developer(
                tenant_id=tenant_id,
                id=person.id,
                name=person.name,
                metadata={
                    "title": person.title,
                    "email": person.email,
                    "handle": person.handle,
                    # Read by /api/v1/auth/dev-users to build the persona picker.
                    "app_roles": ",".join(person.roles),
                    "chat_external_id": person.id,
                    "timezone": person.timezone,
                },
            )
        )
        for pod_id in person.pod_ids:
            await _edge(
                graph,
                tenant_id,
                pod_id,
                person.id,
                valid_from=window_start,
                metadata={"role": person.pod_role},
            )
        await context.registry.status_repository().record_checkin_preference(
            CheckInPreference(
                tenant_id=tenant_id,
                developer_id=person.id,
                local_time=time(9, 30),
                timezone=person.timezone,
                weekdays=(0, 1, 2, 3, 4),
                reply_wait_seconds=14400,
                final_reply_wait_seconds=28800,
            )
        )
        # Without an identity link a member cannot be DM'd, and admin reports
        # them as unmapped. Here the graph id and the chat id coincide, but the
        # link still has to exist -- and it carries the Jira/VCS handles the
        # sync and write-back paths resolve by.
        await context.registry.identity_link_repository().upsert_identity_link(
            IdentityLink(
                tenant_id=tenant_id,
                developer_id=person.id,
                chat_user_id=person.id,
                jira_account_id=person.handle,
                jira_email=person.email,
                vcs_username=person.handle,
            )
        )


async def _seed_work_items(context: SeedContext) -> None:
    """Jira-shaped work items carrying the metadata risk and flow read."""
    graph = context.registry.graph_repository()
    tenant_id = context.tenant_id
    today = context.today

    for item in roster.WORK_ITEMS:
        workstream = _WORKSTREAM_BY_ID[item.workstream_id]
        metadata: dict[str, JsonScalar] = {
            "state": item.state,
            "item_type": item.item_type,
            "owner_id": item.owner_id,
            "created_at": _iso(today - timedelta(days=item.started_days_ago), hour=9),
            "last_transition_at": _iso(today - timedelta(days=item.last_change_days_ago), hour=15),
            "jira_project_key": _PROJECT_BY_ID[workstream.project_id].jira_project_key,
        }
        if item.repo is not None:
            metadata["repo"] = item.repo
            metadata["branch"] = f"feature/{item.id.lower()}"
        if item.pr_id is not None:
            metadata["pr_id"] = item.pr_id
        await graph.upsert_node(
            WorkItem(tenant_id=tenant_id, id=item.id, name=item.name, metadata=metadata)
        )
        await _edge(
            graph,
            tenant_id,
            item.workstream_id,
            item.id,
            valid_from=today - timedelta(days=item.started_days_ago),
        )


async def _seed_tasks(context: SeedContext) -> None:
    """Per-developer task nodes: what the Today focus list ranks."""
    graph = context.registry.graph_repository()
    tenant_id = context.tenant_id
    today = context.today

    for task in roster.TASKS:
        deadline = (
            today + timedelta(days=task.deadline_days_ahead)
            if task.deadline_days_ahead is not None
            else None
        )
        task_metadata: dict[str, JsonScalar] = {
            "status": task.rag,
            "source": StatusSource.CONFIRMED.value,
            "confidence": task.confidence,
            "workstream_id": task.workstream_id,
        }
        if deadline is not None:
            task_metadata["target_date"] = deadline.isoformat()
        await graph.upsert_node(
            TaskNode(tenant_id=tenant_id, id=task.id, name=task.name, metadata=task_metadata)
        )
        # A task hangs off its owner (assigned_to) so it appears in their Today
        # focus list, and off the work item it implements (contains) so the
        # roll-up chain is workstream -> work item -> task with no childless
        # node in the middle reading "unknown".
        await _edge(
            graph,
            tenant_id,
            task.owner_id,
            task.id,
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=today - timedelta(days=WINDOW_DAYS),
        )
        await _edge(
            graph,
            tenant_id,
            task.work_item_id,
            task.id,
            valid_from=today - timedelta(days=WINDOW_DAYS),
        )


async def _edge(
    graph: object,
    tenant_id: str,
    from_node_id: str,
    to_node_id: str,
    *,
    kind: EdgeKind = EdgeKind.CONTAINS,
    valid_from: date | None = None,
    metadata: dict[str, JsonScalar] | None = None,
) -> None:
    """Add an edge, tolerating a re-run over an already-seeded tenant."""
    edge = GraphEdge(
        tenant_id=tenant_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        kind=kind,
        valid_from=valid_from,
        metadata=metadata or {},
    )
    existing = await graph.list_edges(  # type: ignore[attr-defined]
        tenant_id, from_node_id=from_node_id, to_node_id=to_node_id, kind=kind
    )
    if existing:
        return
    await graph.add_edge(edge)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# delivery signals (the "hard" facts status is reconciled against)
# --------------------------------------------------------------------------


async def _seed_delivery_facts(context: SeedContext) -> None:
    time_series = context.registry.time_series_repository()
    tenant_id = context.tenant_id
    today = context.today

    for item in roster.WORK_ITEMS:
        item_ref = EntityRef(tenant_id=tenant_id, kind=NodeKind.WORK_ITEM, id=item.id)
        workstream = _WORKSTREAM_BY_ID[item.workstream_id]
        project = _PROJECT_BY_ID[workstream.project_id]
        transitions = _transition_plan(item)
        for index, (offset, from_state, to_state) in enumerate(transitions):
            observed_at = _at(today - timedelta(days=offset), hour=17, minute=index * 7)
            await time_series.append_fact_once(
                FactEvent(
                    tenant_id=tenant_id,
                    source="work_item",
                    entity_ref=item_ref,
                    payload={
                        "name": item.name,
                        "from_state": from_state,
                        "to_state": to_state,
                        "item_type": item.item_type,
                        "owner_id": item.owner_id,
                        "workstream_id": item.workstream_id,
                        "jira_project_key": project.jira_project_key,
                    },
                    observed_at=observed_at,
                    correlation_id=f"seed-wi-{item.id}-{index}",
                )
            )
            # The tracker's own view of the same move, which the feed renders
            # as an issue update and write-back reconciles against.
            await time_series.append_fact_once(
                FactEvent(
                    tenant_id=tenant_id,
                    source="issue",
                    entity_ref=item_ref,
                    payload={
                        "key": item.id,
                        "title": item.name,
                        "state": to_state,
                        "assignee_id": item.owner_id,
                        "project_key": project.jira_project_key,
                    },
                    observed_at=observed_at + timedelta(minutes=2),
                    correlation_id=f"seed-issue-{item.id}-{index}",
                )
            )

        if item.pr_id is not None and item.repo is not None and item.pr_days_ago is not None:
            opened_at = _at(today - timedelta(days=item.pr_days_ago), hour=18)
            await time_series.append_fact_once(
                FactEvent(
                    tenant_id=tenant_id,
                    source="vcs_pull_request",
                    entity_ref=item_ref,
                    payload={
                        "id": item.pr_id,
                        "repo": item.repo,
                        "title": f"{item.id} {item.name}",
                        "author_id": item.owner_id,
                        "merged": item.pr_merged,
                        "opened_at": opened_at.isoformat(),
                        "branch": f"feature/{item.id.lower()}",
                    },
                    observed_at=opened_at,
                    correlation_id=f"seed-pr-{item.repo}-{item.pr_id}",
                )
            )
            if item.pr_merged:
                merged_at = _at(today - timedelta(days=max(0, item.pr_days_ago - 3)), hour=19)
                await time_series.append_fact_once(
                    FactEvent(
                        tenant_id=tenant_id,
                        source="vcs_pull_request",
                        entity_ref=item_ref,
                        payload={
                            "id": item.pr_id,
                            "repo": item.repo,
                            "title": f"{item.id} {item.name}",
                            "author_id": item.owner_id,
                            "merged": True,
                            "opened_at": opened_at.isoformat(),
                            "merged_at": merged_at.isoformat(),
                        },
                        observed_at=merged_at,
                        correlation_id=f"seed-pr-merge-{item.repo}-{item.pr_id}",
                    )
                )

        # Commits, so repo activity exists for the no-activity drift rule.
        if item.repo is not None and item.state != "proposed":
            rng = _rng(f"commits-{item.id}")
            for commit_index in range(4):
                # Walk backwards from the last state change, but keep recent
                # items pushing in the last few days too.
                day = today - timedelta(days=item.last_change_days_ago + commit_index)
                sha = hashlib.sha1(  # noqa: S324 - display-only identifier
                    f"{item.id}-{commit_index}".encode()
                ).hexdigest()
                await time_series.append_fact_once(
                    FactEvent(
                        tenant_id=tenant_id,
                        source="vcs_commit",
                        entity_ref=item_ref,
                        payload={
                            "sha": sha,
                            "repo": item.repo,
                            "message": (
                                f"{item.id}: {rng.choice(_COMMIT_VERBS)} {item.name.lower()}"
                            ),
                            "author_id": item.owner_id,
                        },
                        observed_at=_at(day, hour=18, minute=commit_index * 11),
                        correlation_id=f"seed-commit-{item.id}-{commit_index}",
                    )
                )

    # Per-task status facts: what the Today focus list reads for confidence.
    for task in roster.TASKS:
        task_ref = EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=task.id)
        await time_series.append_fact_once(
            FactEvent(
                tenant_id=tenant_id,
                source="task",
                entity_ref=task_ref,
                payload={
                    "status": task.rag,
                    "source": StatusSource.CONFIRMED.value,
                    "confidence": task.confidence,
                    "owner_id": task.owner_id,
                },
                observed_at=_at(today - timedelta(days=1), hour=18),
                correlation_id=f"seed-task-{task.id}",
            )
        )


_COMMIT_VERBS = ("wire up", "refactor", "cover", "fix", "tidy", "extend")

_STATE_ORDER = ("proposed", "in_progress", "in_review", "done")


def _transition_plan(item: roster.WorkItemSpec) -> tuple[tuple[int, str, str], ...]:
    """Day offsets and state moves that land the item on its final state."""
    target_index = _STATE_ORDER.index(item.state)
    if target_index == 0:
        return ((item.started_days_ago, "proposed", "proposed"),)
    span = max(1, item.started_days_ago - item.last_change_days_ago)
    steps: list[tuple[int, str, str]] = []
    for index in range(1, target_index + 1):
        # Space the moves evenly between first activity and the last change.
        offset = item.started_days_ago - round(span * (index - 1) / max(1, target_index - 1 or 1))
        if index == target_index:
            offset = item.last_change_days_ago
        steps.append((max(0, offset), _STATE_ORDER[index - 1], _STATE_ORDER[index]))
    return tuple(steps)


# --------------------------------------------------------------------------
# check-in history
# --------------------------------------------------------------------------


async def _seed_checkin_history(context: SeedContext) -> int:
    status_repo = context.registry.status_repository()
    conversations = context.registry.conversation_repository()
    time_series = context.registry.time_series_repository()
    tenant_id = context.tenant_id
    today = context.today

    blocker_rows = _blocker_rows(context)
    for rows in blocker_rows.values():
        await status_repo.record_developer_blockers(tenant_id, rows)

    for person in roster.PEOPLE:
        notes = roster.PROGRESS_NOTES.get(person.id, ("Steady progress",))
        for day_index, day in enumerate(context.days):
            correlation_id = f"seed-{person.id}-{day.isoformat()}"
            asked_at = _at(day, hour=9, minute=30)
            replied = _replied_on(person, day, today)
            open_descriptions = _open_blocker_descriptions(blocker_rows, person.id, day)

            if not replied:
                # Silence is never green: the check-in exists, the status does
                # not become confirmed, and the roll-up reads unknown.
                await status_repo.record_checkin(
                    CheckIn(
                        tenant_id=tenant_id,
                        developer_id=person.id,
                        correlation_id=correlation_id,
                        asked_at=asked_at,
                        replied_at=None,
                        raw_reply=None,
                        signals=None,
                        checkin_date=day,
                    )
                )
                await status_repo.record_developer_status(
                    DeveloperStatus(
                        tenant_id=tenant_id,
                        developer_id=person.id,
                        as_of=day,
                        source=StatusSource.UNKNOWN,
                        blockers=open_descriptions,
                        summary="No reply to the check-in; status is unknown for this day.",
                    )
                )
                await conversations.append_turn(
                    ConversationTurn(
                        tenant_id=tenant_id,
                        developer_id=person.id,
                        conversation_id=f"D{person.id.removeprefix('U')}",
                        conversation_date=day,
                        role=ConversationRole.AGENT,
                        content=_QUESTION,
                        correlation_id=correlation_id,
                        chat_message_id=f"{correlation_id}-q",
                        observed_at=asked_at,
                    )
                )
                await _seed_chat_thread(
                    context,
                    user_id=person.id,
                    correlation_id=correlation_id,
                    asked_at=asked_at,
                )
                continue

            note = notes[day_index % len(notes)]
            eta_change = _eta_change(person, day, bool(open_descriptions))
            reply_text = _reply_text(note, open_descriptions, eta_change)
            replied_at = _clamp_past(
                asked_at + timedelta(minutes=_rng(correlation_id).randint(6, 210))
            )
            signals = CheckInSignals(
                progress_note=note,
                blockers=open_descriptions,
                eta_change_days=eta_change,
                blockers_answered=True,
                eta_answered=True,
            )
            await status_repo.record_checkin(
                CheckIn(
                    tenant_id=tenant_id,
                    developer_id=person.id,
                    correlation_id=correlation_id,
                    asked_at=asked_at,
                    replied_at=replied_at,
                    raw_reply=reply_text,
                    signals=signals,
                    checkin_date=day,
                )
            )
            await status_repo.record_developer_status(
                DeveloperStatus(
                    tenant_id=tenant_id,
                    developer_id=person.id,
                    as_of=day,
                    source=StatusSource.CONFIRMED,
                    blockers=open_descriptions,
                    summary=note,
                    eta_change_days=eta_change,
                    developer_confirmed=True,
                    confirmed_at=replied_at,
                )
            )
            await time_series.append_fact_once(
                FactEvent(
                    tenant_id=tenant_id,
                    source="checkin",
                    entity_ref=EntityRef(
                        tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=person.id
                    ),
                    payload={
                        "status_source": StatusSource.CONFIRMED.value,
                        "blocker_count": len(open_descriptions),
                        "eta_change_days": eta_change,
                        "as_of": day.isoformat(),
                        "developer_name": person.name,
                    },
                    observed_at=replied_at,
                    correlation_id=correlation_id,
                )
            )
            await _seed_chat_thread(
                context,
                user_id=person.id,
                correlation_id=correlation_id,
                asked_at=asked_at,
                reply_text=reply_text,
                replied_at=replied_at,
                ack_text=_ack_text(open_descriptions),
            )
            for role, content, at in (
                (ConversationRole.AGENT, _QUESTION, asked_at),
                (ConversationRole.USER, reply_text, replied_at),
                (
                    ConversationRole.AGENT,
                    _ack_text(open_descriptions),
                    replied_at + timedelta(seconds=20),
                ),
            ):
                await conversations.append_turn(
                    ConversationTurn(
                        tenant_id=tenant_id,
                        developer_id=person.id,
                        conversation_id=f"D{person.id.removeprefix('U')}",
                        conversation_date=day,
                        role=role,
                        content=content,
                        correlation_id=correlation_id,
                        chat_message_id=f"{correlation_id}-{role.value}-{at.timestamp():.0f}",
                        observed_at=at,
                    )
                )

    return sum(len(rows) for rows in blocker_rows.values())


async def _seed_chat_thread(
    context: SeedContext,
    *,
    user_id: str,
    correlation_id: str,
    asked_at: datetime,
    reply_text: str | None = None,
    replied_at: datetime | None = None,
    ack_text: str | None = None,
) -> None:
    """Replay one day's messages into the chat store the console reads.

    Messages are written directly rather than through
    ``inject_chat_simulator_reply``: the status a seeded reply produced is
    already recorded, and re-processing it would re-run the collector against a
    historical date. The correlation row is written too, so an unanswered
    seeded question is a genuinely answerable one -- typing a reply in the
    console routes it back to this check-in.
    """
    if not context.registry.chat_simulator_available():
        return
    store = context.registry._chat_simulator_store()  # noqa: SLF001 - local seeding utility
    channel_id = await store.open_channel(context.tenant_id, user_id)
    question = await store.record_bot_message(
        tenant_id=context.tenant_id,
        channel_id=channel_id,
        text=_QUESTION,
        correlation_id=correlation_id,
        metadata={"purpose": "status_checkin", "seeded": True},
        created_at=asked_at,
    )
    status_repository = context.registry.status_repository()
    await status_repository.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=context.tenant_id,
            correlation_id=correlation_id,
            developer_id=user_id,
            chat_user_ref=user_id,
            chat_thread_ref=channel_id,
            outbound_message_id=question.message_id,
            asked_at=asked_at,
            # An answered day is closed out; an unanswered one stays open so the
            # demo can answer it live.
            consumed_at=replied_at,
        )
    )
    # The scheduler's own record that this person was already asked on this
    # date. Both the daily fan-out and the reconcile sweep skip a developer who
    # has one, so without it the worker re-asks everyone the seed already asked
    # and the threads fill with duplicate questions.
    await status_repository.record_checkin_schedule_run(
        CheckInScheduleRun(
            tenant_id=context.tenant_id,
            developer_id=user_id,
            checkin_date=asked_at.date(),
            correlation_id=correlation_id,
            status="sent",
            scheduled_at=asked_at,
        )
    )
    if reply_text is None or replied_at is None:
        return
    await store.record_user_reply(
        tenant_id=context.tenant_id,
        reply_to_message_id=question.message_id,
        text=reply_text,
        created_at=replied_at,
    )
    if ack_text is not None:
        await store.record_bot_message(
            tenant_id=context.tenant_id,
            channel_id=channel_id,
            text=ack_text,
            correlation_id=correlation_id,
            metadata={"purpose": "status_ack", "seeded": True},
            created_at=_clamp_past(replied_at + timedelta(seconds=20)),
        )


_QUESTION = (
    "Morning! Quick check-in: what moved since yesterday, what is planned today, "
    "and is anything blocking you?"
)


def _ack_text(blockers: Sequence[str]) -> str:
    if blockers:
        return (
            f"Thanks — logged, and I am carrying {len(blockers)} blocker(s) "
            "forward to your pod's board."
        )
    return "Thanks — logged, nothing blocking noted."


def _reply_text(note: str, blockers: Sequence[str], eta_change: int | None) -> str:
    parts = [note + "."]
    if blockers:
        parts.append("Still blocked on: " + "; ".join(blockers) + ".")
    else:
        parts.append("Nothing blocking.")
    if eta_change:
        parts.append(f"ETA slips about {eta_change} day(s).")
    return " ".join(parts)


def _blocker_rows(context: SeedContext) -> dict[str, list[DeveloperBlocker]]:
    """Turn the blocker specs into rows with honest first/last-seen dates."""
    rows: dict[str, list[DeveloperBlocker]] = {}
    today = context.today
    for index, spec in enumerate(roster.BLOCKERS):
        person = _PEOPLE_BY_ID[spec.developer_id]
        first_seen = today + timedelta(days=spec.first_day_offset)
        resolved_on = (
            today + timedelta(days=spec.resolved_day_offset)
            if spec.resolved_day_offset is not None
            else None
        )
        horizon = resolved_on or today
        # Last seen is the most recent day this person actually replied while
        # the blocker was open, which is what makes blocker age credible.
        last_seen = first_seen
        for day in context.days:
            if first_seen <= day <= horizon and _replied_on(person, day, today):
                last_seen = day
        if resolved_on is not None:
            last_seen = max(last_seen, resolved_on)
        rows.setdefault(spec.developer_id, []).append(
            DeveloperBlocker(
                tenant_id=context.tenant_id,
                blocker_id=f"seed-blocker-{index:03d}",
                developer_id=spec.developer_id,
                description=spec.description,
                normalized_key=normalize_blocker_key(spec.description),
                work_item_id=spec.work_item_id,
                pod_id=spec.pod_id,
                source=BlockerSource.CHECKIN,
                source_correlation_id=f"seed-{spec.developer_id}-{first_seen.isoformat()}",
                first_seen_on=first_seen,
                last_seen_on=last_seen,
                resolved_on=resolved_on,
                resolved_reason=(
                    BlockerResolutionReason.REPORTED_RESOLVED if resolved_on is not None else None
                ),
            )
        )
    return rows


def _open_blocker_descriptions(
    rows: dict[str, list[DeveloperBlocker]],
    developer_id: str,
    day: date,
) -> tuple[str, ...]:
    return tuple(
        blocker.description for blocker in rows.get(developer_id, ()) if blocker.is_open_on(day)
    )


def _replied_on(person: roster.Person, day: date, today: date | None = None) -> bool:
    """Deterministic per-person, per-day reply decision.

    History is sampled from ``reply_rate``; the latest day is scripted so the
    demo always opens on the same, explainable picture.
    """
    if today is not None and day == today:
        return not person.silent_today
    return _rng(f"reply-{person.id}-{day.isoformat()}").random() < person.reply_rate


def _eta_change(person: roster.Person, day: date, has_blockers: bool) -> int | None:
    if not has_blockers:
        return None
    rng = _rng(f"eta-{person.id}-{day.isoformat()}")
    if rng.random() < 0.7:
        return None
    return rng.choice((1, 1, 2, 3))


# --------------------------------------------------------------------------
# coordination
# --------------------------------------------------------------------------


async def _seed_cross_person_requests(context: SeedContext) -> None:
    repository = context.registry.cross_person_request_repository()
    time_series = context.registry.time_series_repository()
    tenant_id = context.tenant_id
    today = context.today

    for spec in roster.CROSS_PERSON_REQUESTS:
        requester = _PEOPLE_BY_ID[spec.requester_id]
        counterpart = _PEOPLE_BY_ID[spec.counterpart_id]
        created_at = _at(today + timedelta(days=spec.created_day_offset), hour=10, minute=15)
        updated_at = (
            _at(today + timedelta(days=spec.updated_day_offset), hour=14)
            if spec.updated_day_offset is not None
            else created_at
        )
        existing = await repository.get(tenant_id, spec.id)
        if existing is None:
            await repository.create(
                CrossPersonRequest(
                    tenant_id=tenant_id,
                    id=spec.id,
                    requester_id=spec.requester_id,
                    requester_chat_ref=spec.requester_id,
                    counterpart_id=spec.counterpart_id,
                    kind=CrossPersonRequestKind(spec.kind),
                    note=spec.note,
                    source_correlation_id=(
                        f"seed-{spec.requester_id}-"
                        f"{(today + timedelta(days=spec.created_day_offset)).isoformat()}"
                    ),
                    status=CrossPersonRequestStatus(spec.status),
                    created_at=created_at,
                    updated_at=updated_at,
                    task_ref=(
                        EntityRef(
                            tenant_id=tenant_id,
                            kind=NodeKind.WORK_ITEM,
                            id=spec.work_item_id,
                        )
                        if spec.work_item_id is not None
                        else None
                    ),
                    raw_name=counterpart.name,
                    email=counterpart.email,
                    counterpart_display_name=counterpart.name,
                    counterpart_email=counterpart.email,
                )
            )
        for transition, at in _cross_person_transitions(spec, created_at, updated_at):
            await time_series.append_fact_once(
                FactEvent(
                    tenant_id=tenant_id,
                    source="cross_person_request",
                    entity_ref=EntityRef(
                        tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=spec.counterpart_id
                    ),
                    payload={
                        "transition": transition,
                        "requester_id": spec.requester_id,
                        "requester_name": requester.name,
                        "counterpart_id": spec.counterpart_id,
                        "counterpart_name": counterpart.name,
                        "kind": spec.kind,
                        "note": spec.note,
                        "request_id": spec.id,
                    },
                    observed_at=at,
                    correlation_id=f"seed-xpr-{spec.id}-{transition}",
                )
            )


def _cross_person_transitions(
    spec: roster.CrossPersonSpec,
    created_at: datetime,
    updated_at: datetime,
) -> tuple[tuple[str, datetime], ...]:
    steps: list[tuple[str, datetime]] = [("opened", created_at)]
    if spec.status in {"acknowledged", "resolved"}:
        steps.append(("acknowledged", updated_at))
    if spec.status == "resolved":
        steps.append(("resolved", updated_at + timedelta(hours=2)))
    return tuple(steps)


# --------------------------------------------------------------------------
# derived passes: rollups, risk, drift, briefs
# --------------------------------------------------------------------------


async def _run_rollups(context: SeedContext) -> None:
    registry = context.registry
    service = RollupService(
        registry.status_repository(),
        registry.rollup_repository(),
        BlockerResolutionService(registry.graph_repository(), registry.status_repository()),
    )
    graph = registry.graph_repository()
    for day in context.days:
        tree = await graph.get_program_tree(context.tenant_id, roster.PROGRAM_ID, day)
        await service.compute_and_record(tree, day)


async def _run_risk_and_drift(context: SeedContext) -> None:
    registry = context.registry
    settings = registry.settings
    service = RiskService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        status_repository=registry.status_repository(),
        blocker_resolution=BlockerResolutionService(
            registry.graph_repository(), registry.status_repository()
        ),
        rollup_repository=registry.rollup_repository(),
        provider_config=RiskProviderConfig(
            jira_base_url=settings.jira_base_url,
            github_base_url=settings.github_base_url,
            default_no_pr_days=settings.risk_default_no_pr_days,
            default_pr_age_days=settings.risk_default_pr_age_days,
            default_stale_days=settings.risk_default_stale_days,
            default_no_activity_days=settings.drift_no_activity_days,
        ),
    )
    for day in context.days[-RISK_TAIL_DAYS:]:
        for project in roster.PROJECTS:
            await service.assess_and_persist_project(context.tenant_id, project.id, day)
            await service.scan_and_record_drift(context.tenant_id, project.id, day)


async def _seed_briefs(context: SeedContext) -> None:
    registry = context.registry
    persona_views = PersonaViewService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        rollup_repository=registry.rollup_repository(),
        time_series_repository=registry.time_series_repository(),
    )
    service = NarrativeBriefService(
        registry.llm_provider(),
        PortfolioFeedService(registry.time_series_repository()),
        persona_views,
        registry.graph_repository(),
        registry.narrative_brief_repository(),
        registry.settings.default_llm_model,
    )
    # A few days of each kind so the Coordination column has a real history.
    for offset in (0, 1, 2):
        generated_at = _at(context.today - timedelta(days=offset), hour=18)
        for pod in roster.PODS:
            await service.generate(
                context.tenant_id, BriefKind.DAILY_POD, pod.id, as_of=generated_at
            )
    for offset in (0, 7):
        generated_at = _at(context.today - timedelta(days=offset), hour=18, minute=30)
        for project in roster.PROJECTS:
            await service.generate(
                context.tenant_id, BriefKind.WEEKLY_PROJECT, project.id, as_of=generated_at
            )
        await service.generate(
            context.tenant_id, BriefKind.EXEC, roster.PROGRAM_ID, as_of=generated_at
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _weekdays(end: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    cursor = end - timedelta(days=count)
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


# Wall clock captured once when seeding starts. Everything seeded is dated
# before it: a run early in the morning would otherwise stamp "today" events at
# 09:30 or 18:00, putting them in the future where they sort ahead of anything
# typed live in the demo.
_SEED_NOW = datetime.now(tz=UTC)
_SEED_MARGIN = timedelta(minutes=5)


def _at(day: date, *, hour: int, minute: int = 0) -> datetime:
    """A timestamp on ``day``, folded into the past if the day is still running.

    Times on a finished day are used as written. On today, the 24-hour clock is
    compressed into the part of the day that has actually happened, which keeps
    the relative order of a 09:30 check-in and an 18:00 commit intact.
    """
    stamp = datetime(day.year, day.month, day.day, hour, minute % 60, tzinfo=UTC)
    latest = _SEED_NOW - _SEED_MARGIN
    if stamp <= latest:
        return stamp
    start_of_day = datetime(day.year, day.month, day.day, tzinfo=UTC)
    if latest <= start_of_day:
        # Seeding just after midnight: nothing of today has happened yet.
        return latest
    elapsed = (latest - start_of_day).total_seconds()
    fraction = (hour * 60 + minute % 60) / (24 * 60)
    return start_of_day + timedelta(seconds=elapsed * fraction)


def _clamp_past(value: datetime) -> datetime:
    """Never let a derived timestamp (a reply, an ack) run into the future."""
    return min(value, _SEED_NOW - _SEED_MARGIN)


def _iso(day: date, *, hour: int) -> str:
    return _at(day, hour=hour).isoformat()


def _rng(seed: str) -> random.Random:
    return random.Random(seed)  # noqa: S311 - reproducible demo data, not crypto


if __name__ == "__main__":
    sys.exit(main())
