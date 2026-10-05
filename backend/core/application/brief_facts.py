"""The facts a narrative brief is written from: its scope's, read whole, said plainly.

A brief used to be written from the newest twelve items of the portfolio-wide
feed (N38, N39). Every pod's brief so claimed other pods' issues, the exec
brief missed a blocker the thirteenth item reported, a merged merge request
read as a finished ticket, and a superseded copy of an ask read as a
rescheduled review. ``build_brief_facts`` reads every feed item of the window
instead, keeps the ones inside the brief's scope and aggregates them: one line
per issue saying what the tracker says, per request, per person. The model
writes from those lines, and ``brief_grounding`` checks what it wrote against
the same ``BriefFacts``.

The lines say who did what, so a sentence can be checked for it (N51-N53):
who reported each blocker and whom they waited on, who opened and who merged
each merge request (the merge commit's author), and each ETA change with the
check-in that gave it -- counted only when the parser that recorded it told an
ETA that moved from a duration (``eta_change_checked``, N45), since an older
fact's number cannot be told apart without the reply's text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from core.application.merge_request_links import ISSUE_KEY as _FACT_ISSUE_KEY
from core.application.persona_views import (
    NO_POD_ROW,
    HeatmapCellView,
    PodCheckinsView,
    ProjectProgressView,
)
from core.application.portfolio_feed_service import PortfolioFeedItemView
from core.domain.graph import GraphNode, JsonScalar, NodeKind
from core.domain.rollup import Rag
from core.domain.status import CheckInDay

# The heat-map cells that carry a status of their own. A repository, a sprint
# or a workstream cell is unknown by construction (N38: '14 unknown cells').
STATUS_BEARING_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.DEVELOPER, NodeKind.POD, NodeKind.PROJECT, NodeKind.PROGRAM}
)

# An issue key as a sentence names it: "CHK-17", whole and upper case.
SENTENCE_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])")

# Request transitions that close a copy of an ask without anyone doing it: a
# superseded copy (N11b) or a dismissed one. Bookkeeping, not delivery.
_CLOSED_COPY_TRANSITIONS = frozenset({"superseded", "dismissed"})
_REQUEST_STATE_ORDER = ("needs_resolution", "opened", "acknowledged", "resolved")
_REQUEST_STATE_LABELS = {
    "needs_resolution": "Requests that need a PM to resolve them",
    "opened": "Open requests",
    "acknowledged": "Requests being handled",
    "resolved": "Requests resolved",
}
_REQUEST_KIND_LABELS = {
    "needs_review": "review",
    "needs_input": "input",
    "blocked_by": "blocker",
    "waiting_on": "dependency",
}
_KIND_NOUNS: dict[NodeKind, tuple[str, str]] = {
    NodeKind.DEVELOPER: ("team member", "team members"),
    NodeKind.POD: ("pod", "pods"),
    NodeKind.PROJECT: ("project", "projects"),
    NodeKind.PROGRAM: ("program", "programs"),
}
_RAG_ORDER = (Rag.GREEN, Rag.AMBER, Rag.RED, Rag.UNKNOWN)
_MAX_NOT_GREEN = 10
_MAX_PER_SECTION = 12
_MAX_EARLIER_BLOCKED = 3
_MAX_SOURCES = 20
# A merge commit names its merge request: GitLab's "See merge request
# group/repo!7", GitHub's "Merge pull request #7 from ...". Its author merged it.
_GITLAB_MERGE = re.compile(r"See merge request (?P<repo>[\w.\-/]+)!(?P<id>\d+)")
_GITHUB_MERGE = re.compile(r"Merge pull request #(?P<id>\d+)\b")
# Request kinds that say the reporter is waiting on someone: a blocker's owner.
_WAIT_KINDS = frozenset({"waiting_on", "blocked_by"})
# A merge request named by its number alone ("merge !1", "PR #4"): a reader
# cannot tell which repository's it is, so a line says which it was.
BARE_MERGE_REQUEST = re.compile(
    r"(?:\b(?:merge|pull)\s+requests?\s+|\b(?:merge|MR|PR|review)\s+)?(?<![\w/.\-])[!#](\d+)\b"
)
# The heat-map cell line a person in no team carries (persona_views): said apart.
_NO_POD_LEAD = "No pod, outside team colours: "


@dataclass(frozen=True, kw_only=True)
class StatusFacts:
    """The status sentence of a brief and the counts it states, word by word."""

    sentence: str
    #: Each status word the sentence counts ("green", "confirmed", ...) with its
    #: count, zeros included, so a sentence claiming another count is caught.
    counts: Mapping[str, int] = field(default_factory=dict)
    detail_lines: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class BriefScope:
    """What a brief covers. ``None`` is the whole tenant, as the exec brief reads it.

    ``member_ids`` are the people whose check-ins count, ``issue_ids`` the
    task nodes whose issues do, ``node_ids`` every node the scope contains
    (work items, repositories) and ``repos`` the repositories by full name.
    """

    member_ids: frozenset[str] | None = None
    member_names: tuple[str, ...] = ()
    issue_ids: frozenset[str] | None = None
    node_ids: frozenset[str] | None = None
    repos: frozenset[str] | None = None

    @classmethod
    def empty(cls) -> BriefScope:
        """A scope that holds nothing: its root is not in the graph."""
        return cls(
            member_ids=frozenset(),
            issue_ids=frozenset(),
            node_ids=frozenset(),
            repos=frozenset(),
        )

    @property
    def whole_tenant(self) -> bool:
        return self.issue_ids is None


@dataclass(frozen=True, kw_only=True)
class BriefInputs:
    label: str
    scope_name: str
    as_of: datetime
    since: datetime
    status: StatusFacts
    scope: BriefScope
    #: Feed items of the window, any order, unscoped: scoping happens here.
    items: Sequence[PortfolioFeedItemView]
    #: Every task of the tenant, by node id.
    tasks: Mapping[str, GraphNode]
    #: Every person of the tenant: node id to name.
    people: Mapping[str, str]
    #: Every repository of the tenant, by full name.
    repos: frozenset[str] = frozenset()
    #: Blockers open now per person id; ``None`` when they could not be read.
    open_blockers: Mapping[str, int] | None = None
    #: The ref the brief is about ("pod:pod-1"), cited first.
    scope_ref: str | None = None
    #: The brief's day's check-ins of the people in scope: who was asked and
    #: who answered; ``None`` when they could not be read.
    checkins_today: Sequence[CheckInDay] | None = None
    #: The day's headline as Exec Today shows it (``attention``), for the
    #: exec brief: the same cause and the same team counts as the hero.
    headline: str | None = None


@dataclass(frozen=True, kw_only=True)
class IssueFact:
    key: str
    title: str
    #: The tracker's normalized state ("done", "in_progress", "todo") and its
    #: own status name ("In Progress"), as the issue sync last stored them.
    tracker_state: str | None
    tracker_status: str | None
    #: The merge requests of the window that name the issue and merged.
    merged: tuple[str, ...] = ()

    @property
    def done(self) -> bool:
        """Done only when the tracker says so: a merge alone never is."""
        return (self.tracker_state or "").lower() == "done"

    @property
    def merged_ticket_open(self) -> bool:
        return bool(self.merged) and not self.done

    @property
    def tracker_label(self) -> str:
        if self.tracker_status:
            return self.tracker_status
        if self.tracker_state:
            return self.tracker_state.replace("_", " ")
        return "no status"


@dataclass(frozen=True, kw_only=True)
class BriefFacts:
    """What a brief may say, and what its sentences are checked against."""

    lines: tuple[str, ...]
    status: StatusFacts
    #: Issues the lines describe, by key.
    issues: Mapping[str, IssueFact]
    #: Every issue key the lines name; a sentence may name no other.
    issue_keys: frozenset[str]
    #: The key prefixes of the tenant's issues ("CHK"): a token like "SHA-256"
    #: is not an issue, "CHK-99" is one the facts do not name.
    known_key_prefixes: frozenset[str]
    #: The people the lines name, and every person of the tenant.
    people: frozenset[str]
    known_people: frozenset[str]
    blocker_sentence: str
    blockers_reported: int
    blockers_open: int
    #: Whether any ETA moved later in the window.
    eta_slips: bool
    sources: tuple[str, ...]
    #: Who a blocker may be said to be of: the people whose check-ins of the
    #: window reported one, and those with one open now.
    blocker_people: frozenset[str] = frozenset()
    #: The window's merge requests: who opened each and who merged it.
    merges: tuple[MergeFact, ...] = ()
    #: Each person's ETA changes of the window, by name, oldest first.
    eta_changes: Mapping[str, tuple[EtaChange, ...]] = field(default_factory=dict)
    #: The verdict a brief stands on when the model's is unusable: the day's
    #: headline when there is one, else the status sentence.
    verdict_fallback: str = ""
    #: Short sentences, true by construction, saying who needs to act on what
    #: today, for a brief whose bullets ran short.
    action_lines: tuple[str, ...] = ()

    @property
    def context(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True, kw_only=True)
class MergeFact:
    """A merge request of the window, as a sentence may name it and its people."""

    #: How a reader names it: "insights-pipeline !1" (GitLab), "api #4" (GitHub).
    label: str
    #: Its number and repository as people write them ("7", "web").
    number: str
    repo: str
    #: The issue keys it names.
    keys: frozenset[str]
    merged: bool
    #: Who opened it, and who merged it (the merge commit's author), when known.
    author: str | None
    merger: str | None

    @property
    def named(self) -> str:
        """Its name with its ticket: "insights-pipeline !1 (INS-2)"."""
        keys = sorted(self.keys, key=_key_order)
        return f"{self.label} ({', '.join(keys)})" if keys else self.label


@dataclass(frozen=True, kw_only=True)
class EtaChange:
    """An ETA change one check-in reported, in days (later is positive)."""

    days: int
    at: datetime
    #: Whether it came with the person's latest check-in of the window.
    latest: bool


@dataclass(frozen=True, kw_only=True)
class _Checkin:
    at: datetime
    source: str
    blockers: int
    eta_change_days: int | None


@dataclass(frozen=True, kw_only=True)
class _Request:
    item: PortfolioFeedItemView
    transition: str


def heatmap_status(cells: Iterable[HeatmapCellView]) -> StatusFacts:
    """The portfolio's status over the cells that carry one (N38).

    People, pods, projects and the program carry a status; a repository, a
    sprint or a workstream cell is unknown by construction, so counting it
    reported 'unknown' for what has no status at all. A person in no team (the
    heat map's "no pod" row) counts in no team's colour, so they are said
    apart, by name, and never counted: the counts are the hero's.
    """
    status_bearing = [cell for cell in cells if cell.entity_ref.kind in STATUS_BEARING_KINDS]
    bearing = [cell for cell in status_bearing if cell.row != NO_POD_ROW]
    outside = [cell for cell in status_bearing if cell.row == NO_POD_ROW]
    counts = {rag.value: 0 for rag in _RAG_ORDER}
    for cell in bearing:
        counts[cell.rag.value] = counts.get(cell.rag.value, 0) + 1
    outside_lines = tuple(_outside_line(cell) for cell in outside)
    if not bearing:
        return StatusFacts(
            sentence="No status is recorded yet for people, pods, projects or the program.",
            counts=counts,
            detail_lines=outside_lines,
        )
    kinds: dict[NodeKind, int] = {}
    for cell in bearing:
        kinds[cell.entity_ref.kind] = kinds.get(cell.entity_ref.kind, 0) + 1
    covered = _join_and(
        _kind_count(kind, kinds[kind])
        for kind in (NodeKind.DEVELOPER, NodeKind.POD, NodeKind.PROJECT, NodeKind.PROGRAM)
        if kind in kinds
    )
    summary = ", ".join(
        f"{counts[rag.value]} {rag.value}" for rag in _RAG_ORDER if counts[rag.value]
    )
    details = _not_green_lines(bearing)[:_MAX_NOT_GREEN] + outside_lines
    return StatusFacts(
        sentence=f"Status of {covered}: {summary}.", counts=counts, detail_lines=details
    )


def _not_green_lines(cells: Sequence[HeatmapCellView]) -> tuple[str, ...]:
    """What is not green and why: people with the same reason on one line, then each team."""
    people: dict[tuple[Rag, str], list[str]] = {}
    teams: list[str] = []
    for cell in cells:
        if cell.rag is Rag.GREEN:
            continue
        why = cell.why.rstrip(".") if cell.why else ""
        if cell.entity_ref.kind is NodeKind.DEVELOPER:
            people.setdefault((cell.rag, why), []).append(cell.name or "a team member")
            continue
        name = cell.name or "unnamed " + cell.entity_ref.kind.value
        teams.append(f"{cell.rag.value.capitalize()}: {name}" + (f" ({why})" if why else "") + ".")
    grouped = [
        f"{rag.value.capitalize()} ({why or 'no reason recorded'}): {_join_and(names)}."
        for (rag, why), names in people.items()
    ]
    return (*grouped, *teams)


def _outside_line(cell: HeatmapCellView) -> str:
    why = cell.why.removeprefix(_NO_POD_LEAD).rstrip(".") if cell.why else ""
    name = cell.name or "a team member"
    return (
        f"In no team, so in none of the counts: {name}, {cell.rag.value}"
        + (f" ({why})" if why else "")
        + "."
    )


def pod_status(view: PodCheckinsView) -> StatusFacts:
    return StatusFacts(
        sentence=(
            f"Check-ins: {view.confirmed} confirmed, {view.partial} partial, "
            f"{view.stale} stale, {view.missing} missing "
            f"across {len(view.developers)} member(s)."
        ),
        counts={
            "confirmed": view.confirmed,
            "partial": view.partial,
            "stale": view.stale,
            "missing": view.missing,
        },
    )


def project_status(progress: ProjectProgressView) -> StatusFacts:
    return StatusFacts(
        sentence=(
            f"Status {progress.rag.value} ({progress.source.value}); "
            f"{progress.percent_complete:.0f}% complete across "
            f"{progress.total_tasks} task(s) "
            f"({progress.green_tasks} green, {progress.amber_tasks} amber, "
            f"{progress.red_tasks} red, {progress.unknown_tasks} unknown)."
        ),
        counts={
            "green": progress.green_tasks,
            "amber": progress.amber_tasks,
            "red": progress.red_tasks,
            "unknown": progress.unknown_tasks,
        },
    )


def no_status() -> StatusFacts:
    return StatusFacts(sentence="No rollup status is available yet.")


def task_key(node: GraphNode) -> str | None:
    """The issue key a task carries ("CHK-17"), else its id when that is one."""
    key = node.metadata.get("key")
    if isinstance(key, str) and SENTENCE_ISSUE_KEY.fullmatch(key.strip().upper()):
        return key.strip().upper()
    if SENTENCE_ISSUE_KEY.fullmatch(node.id):
        return node.id
    return None


def build_brief_facts(inputs: BriefInputs) -> BriefFacts:
    """Scope, aggregate and render the window's feed items for one brief."""
    reader = _ScopeReader(inputs)
    collected = _Collected(reader=reader, people=inputs.people)
    for item in sorted(
        (item for item in inputs.items if item.observed_at <= inputs.as_of),
        key=lambda item: item.observed_at,
    ):
        collected.add(item)
    issues = _issue_facts(inputs, reader, collected.issue_moves, collected.merge_requests)
    blockers = _blocker_facts(
        inputs,
        collected.checkins,
        collected.checkin_names,
        collected.resolved_requests,
        collected.waits,
    )
    merges = _merge_facts(collected.merge_requests, collected.mergers, reader)
    lines = _context_lines(inputs, collected, issues, blockers, merges)
    actions = _action_lines(inputs, collected, issues, merges)
    context = "\n".join(lines)
    known_people = frozenset(
        name for person_id, name in inputs.people.items() if _usable_person(name, person_id)
    )
    task_by_key = {key: node for node in inputs.tasks.values() if (key := task_key(node))}
    issue_keys = frozenset(
        set(SENTENCE_ISSUE_KEY.findall(context)) | (_fact_keys(context) & set(task_by_key))
    )
    return BriefFacts(
        lines=lines,
        status=inputs.status,
        issues=_tracked_issues(issues, issue_keys, task_by_key),
        issue_keys=issue_keys,
        known_key_prefixes=frozenset(key.rsplit("-", 1)[0] for key in task_by_key),
        people=frozenset(name for name in known_people if name in context),
        known_people=known_people,
        blocker_sentence=blockers.sentence,
        blockers_reported=blockers.reported,
        blockers_open=blockers.open_now,
        eta_slips=collected.eta_slips,
        sources=_sources(inputs.scope_ref, collected.cited),
        blocker_people=blockers.people,
        merges=merges,
        eta_changes=_eta_changes(collected.checkins, collected.checkin_names),
        verdict_fallback=inputs.headline or inputs.status.sentence,
        action_lines=actions,
    )


def _context_lines(
    inputs: BriefInputs,
    collected: _Collected,
    issues: Mapping[str, IssueFact],
    blockers: _BlockerFacts,
    merges: Sequence[MergeFact],
) -> tuple[str, ...]:
    lines: list[str] = [
        f"{inputs.label} for {inputs.scope_name}.",
        f"Window: since {_clock(inputs.since, inputs.as_of)}.",
        *(
            [f"The day's headline, as Exec Today shows it: {inputs.headline}"]
            if inputs.headline
            else []
        ),
        inputs.status.sentence,
        *inputs.status.detail_lines,
        *_checkin_day_lines(inputs),
    ]
    if inputs.scope.member_names:
        lines.append(f"Members: {', '.join(inputs.scope.member_names)}.")
    activity = [
        *_issue_lines(issues),
        *_merge_request_lines(collected.merge_requests, merges, inputs.as_of),
        *_checkin_lines(
            collected.checkins, collected.checkin_names, inputs.as_of, _people_in_scope(inputs)
        ),
        *_request_lines(collected.requests, inputs.as_of, merges),
        *_risk_lines(collected.risks),
        *_work_item_lines(collected.work_items),
        *_commit_lines(collected.commits),
    ]
    lines.extend(activity or ["No recent activity in the window."])
    # Always said, cleared blockers included: 'no blockers' must not be inferred
    # from a window whose check-ins reported one (N38).
    lines.append(f"Blockers: {blockers.line}")
    return tuple(lines)


def _checkin_day_lines(inputs: BriefInputs) -> list[str]:
    """The brief's own day's check-in: how many were asked, when, and how many answered.

    A window's check-ins say who answered at some point in the week; this says
    whether today's statuses come from answers at all.
    """
    day = inputs.checkins_today
    if not day:
        return []
    answered = sum(1 for checkin in day if checkin.answered)
    first = min(checkin.first_asked_at for checkin in day)
    on = f"{inputs.as_of.astimezone(UTC).day} {inputs.as_of.astimezone(UTC):%b}"
    if answered == 0:
        tail = "none answered yet; their statuses for the day are inferred or carried over"
    elif answered == len(day):
        tail = "all answered"
    else:
        tail = f"{answered} answered"
    who = _count(len(day), ("team member", "team members"))
    return [f"Check-in of {on}: {who} asked (first at {_clock(first, inputs.as_of)}), {tail}."]


def _tracked_issues(
    issues: Mapping[str, IssueFact],
    issue_keys: frozenset[str],
    task_by_key: Mapping[str, GraphNode],
) -> dict[str, IssueFact]:
    """What the tracker says of every issue the lines name, not only the ones that moved.

    A request's ask or a status reason can name an issue too.
    """
    tracked = dict(issues)
    for key in sorted(issue_keys - set(issues)):
        if (node := task_by_key.get(key)) is not None:
            tracked[key] = _tracked_issue(key, node)
    return tracked


@dataclass(kw_only=True)
class _Collected:
    """The window's feed items inside a brief's scope, aggregated as they are read."""

    reader: _ScopeReader
    people: Mapping[str, str]
    checkins: dict[str, list[_Checkin]] = field(default_factory=dict)
    checkin_names: dict[str, str] = field(default_factory=dict)
    issue_moves: dict[str, tuple[str | None, datetime]] = field(default_factory=dict)
    merge_requests: dict[tuple[str, str], PortfolioFeedItemView] = field(default_factory=dict)
    requests: dict[str, _Request] = field(default_factory=dict)
    #: When each person's requests were resolved, whatever the scope: a sign
    #: of when their wait ended.
    resolved_requests: dict[str, list[datetime]] = field(default_factory=dict)
    commits: dict[str, int] = field(default_factory=dict)
    risks: dict[tuple[str, str], PortfolioFeedItemView] = field(default_factory=dict)
    work_items: dict[str, PortfolioFeedItemView] = field(default_factory=dict)
    #: Who merged each merge request, (repo, number): its merge commit's author.
    mergers: dict[tuple[str, str], str] = field(default_factory=dict)
    #: Whom each person waited on in the window, by person id: "Ben Okafor for SHOP-2".
    waits: dict[str, list[str]] = field(default_factory=dict)
    #: The ref behind each aggregated line, by the slot it fills, with when.
    cites: dict[tuple[str, str], tuple[datetime, str]] = field(default_factory=dict)

    def add(self, item: PortfolioFeedItemView) -> None:
        handlers = {
            "cross_person_request": self._request,
            "checkin": self._checkin,
            "issue": self._issue,
            "vcs_pull_request": self._merge_request,
            "vcs_commit": self._commit,
            "risk": self._risk,
            "work_item": self._work_item,
        }
        handler = handlers.get(item.source)
        slot = handler(item) if handler is not None else None
        if slot is not None:
            self.cites[(item.source, slot)] = (item.observed_at, _ref(item))

    @property
    def cited(self) -> list[str]:
        """The refs of what the lines say, newest first."""
        return [
            ref for _, ref in sorted(self.cites.values(), key=lambda cite: cite[0], reverse=True)
        ]

    @property
    def eta_slips(self) -> bool:
        return any(
            (checkin.eta_change_days or 0) > 0
            for history in self.checkins.values()
            for checkin in history
        )

    def _request(self, item: PortfolioFeedItemView) -> str | None:
        transition = _detail_str(item.details, "transition") or "opened"
        request_id = _detail_str(item.details, "request_id") or item.entity_ref.id
        reporter_id = _detail_str(item.details, "reporter_id")
        if transition == "resolved" and reporter_id:
            self.resolved_requests.setdefault(reporter_id, []).append(item.observed_at)
        if transition in _CLOSED_COPY_TRANSITIONS:
            # A copy closed for a newer one is no event of its own: the newest
            # copy carries the ask (N39: read as 'rescheduled').
            self.requests.pop(request_id, None)
            self.cites.pop((item.source, request_id), None)
            return None
        self._wait(item, reporter_id)
        if not self.reader.request_in_scope(item):
            return None
        self.requests[request_id] = _Request(item=item, transition=transition)
        return request_id

    def _wait(self, item: PortfolioFeedItemView, reporter_id: str | None) -> None:
        """Whom a person waits on, from their dependency asks: who owns their blocker."""
        kind = _detail_str(item.details, "dependency_kind")
        counterpart = _detail_str(item.details, "referenced_person_name")
        if reporter_id is None or kind not in _WAIT_KINDS or counterpart is None:
            return
        keys = sorted(self.reader.named_keys(_detail_str(item.details, "summary") or ""))
        wait = f"{counterpart} for {_join_and(keys)}" if keys else counterpart
        waits = self.waits.setdefault(reporter_id, [])
        if wait not in waits:
            waits.append(wait)

    def _checkin(self, item: PortfolioFeedItemView) -> str | None:
        person_id = item.entity_ref.id
        if not self.reader.member(person_id):
            return None
        # Only a parser that tells an ETA that moved from a duration (N45)
        # marks the fact; an older number may be a duration ("2-3 days").
        checked = item.details.get("eta_change_checked") is True
        self.checkins.setdefault(person_id, []).append(
            _Checkin(
                at=item.observed_at,
                source=_detail_str(item.details, "status_source") or "confirmed",
                blockers=_detail_int(item.details, "blocker_count") or 0,
                eta_change_days=_detail_int(item.details, "eta_change_days") if checked else None,
            )
        )
        self.checkin_names[person_id] = item.person_name or self.people.get(person_id, "")
        return person_id

    def _issue(self, item: PortfolioFeedItemView) -> str | None:
        key = self.reader.issue_key(item)
        if key is not None:
            self.issue_moves[key] = (_detail_str(item.details, "state"), item.observed_at)
        return key

    def _merge_request(self, item: PortfolioFeedItemView) -> str | None:
        repo = _detail_str(item.details, "repo")
        number = _detail_str(item.details, "id")
        if repo is None or number is None or not self.reader.code_in_scope(item, repo):
            return None
        self.merge_requests[(repo, number)] = item
        return f"{repo}!{number}"

    def _commit(self, item: PortfolioFeedItemView) -> str | None:
        repo = _detail_str(item.details, "repo")
        if repo is None:
            return None
        merged = _merged_request(item.summary, repo)
        if merged is not None and item.person_name:
            # The merge commit's author is who merged it; the request's own
            # fact only knows who opened it (N52).
            self.mergers[merged] = item.person_name
        if self.reader.code_in_scope(item, repo):
            self.commits[repo] = self.commits.get(repo, 0) + 1
        return None

    def _risk(self, item: PortfolioFeedItemView) -> str | None:
        if not self.reader.node_in_scope(item.entity_ref.id):
            return None
        rule = _detail_str(item.details, "rule_id") or item.kind
        self.risks[(rule, item.entity_ref.id)] = item
        return f"{rule}:{item.entity_ref.id}"

    def _work_item(self, item: PortfolioFeedItemView) -> str | None:
        if not self.reader.node_in_scope(item.entity_ref.id):
            return None
        self.work_items[item.entity_ref.id] = item
        return item.entity_ref.id


class _ScopeReader:
    """Which feed items belong to a brief's scope.

    The exec brief reads the whole tenant. A pod or project brief reads its
    members' check-ins and its own issues only (N39): a merge request, commit
    or request that names an issue counts when every issue it names is in
    scope, one that names none when its repository (or, for a request, a
    person in it) is.
    """

    def __init__(self, inputs: BriefInputs) -> None:
        self._scope = inputs.scope
        self._key_by_task = {
            task_id: key for task_id, node in inputs.tasks.items() if (key := task_key(node))
        }
        self._tenant_keys = frozenset(self._key_by_task.values())
        self._scope_keys = (
            None
            if inputs.scope.issue_ids is None
            else frozenset(
                self._key_by_task[task_id]
                for task_id in inputs.scope.issue_ids
                if task_id in self._key_by_task
            )
        )
        self._repo_names = {
            _repo_short_name(repo): repo for repo in inputs.repos | (inputs.scope.repos or set())
        }

    @property
    def key_by_task(self) -> Mapping[str, str]:
        return self._key_by_task

    def member(self, person_id: str) -> bool:
        members = self._scope.member_ids
        return members is None or person_id in members

    def issue_key(self, item: PortfolioFeedItemView) -> str | None:
        """The key of an issue item in scope, else None."""
        key = self._key_by_task.get(item.entity_ref.id)
        if key is None:
            detail = _detail_str(item.details, "key")
            key = detail.strip().upper() if detail else None
        if key is None:
            return None
        if self._scope.issue_ids is None:
            return key
        in_scope = item.entity_ref.id in self._scope.issue_ids or (
            self._scope_keys is not None and key in self._scope_keys
        )
        return key if in_scope else None

    def named_keys(self, text: str) -> frozenset[str]:
        return frozenset(_fact_keys(text) & self._tenant_keys)

    def keys_in_scope(self, keys: frozenset[str]) -> bool:
        return self._scope_keys is None or keys <= self._scope_keys

    def code_in_scope(self, item: PortfolioFeedItemView, repo: str) -> bool:
        if self._scope.whole_tenant:
            return True
        keys = self.named_keys(item.summary)
        if keys:
            return self.keys_in_scope(keys)
        repos = self._scope.repos or frozenset()
        return repo in repos or _repo_short_name(repo) in {_repo_short_name(r) for r in repos}

    def request_in_scope(self, item: PortfolioFeedItemView) -> bool:
        if self._scope.whole_tenant:
            return True
        summary = _detail_str(item.details, "summary") or ""
        keys = self.named_keys(summary)
        if keys:
            return self.keys_in_scope(keys)
        named_repos = {
            full
            for short, full in self._repo_names.items()
            if re.search(rf"(?<![\w-]){re.escape(short)}(?![\w-])", summary, re.IGNORECASE)
        }
        if named_repos:
            scope_short = {_repo_short_name(repo) for repo in self._scope.repos or frozenset()}
            return all(_repo_short_name(repo) in scope_short for repo in named_repos)
        people = (
            _detail_str(item.details, "reporter_id"),
            _detail_str(item.details, "referenced_person_id"),
        )
        return any(person is not None and self.member(person) for person in people)

    def node_in_scope(self, node_id: str) -> bool:
        scope = self._scope
        if scope.whole_tenant:
            return True
        return (
            node_id in (scope.node_ids or frozenset())
            or node_id in (scope.issue_ids or frozenset())
            or node_id in (scope.member_ids or frozenset())
        )


def _issue_facts(
    inputs: BriefInputs,
    reader: _ScopeReader,
    issue_moves: Mapping[str, tuple[str | None, datetime]],
    merge_requests: Mapping[tuple[str, str], PortfolioFeedItemView],
) -> dict[str, IssueFact]:
    task_by_key = {key: inputs.tasks[task_id] for task_id, key in reader.key_by_task.items()}
    named: set[str] = set(issue_moves)
    merged: dict[str, list[str]] = {}
    for (repo, number), item in sorted(merge_requests.items()):
        for key in sorted(reader.named_keys(item.summary)):
            named.add(key)
            if item.details.get("merged") is True:
                merged.setdefault(key, []).append(_merge_request_label(repo, number, item))
    facts: dict[str, IssueFact] = {}
    for key in sorted(named, key=_key_order):
        tracked = _tracked_issue(key, task_by_key.get(key))
        moved_to, _ = issue_moves.get(key, (None, None))
        facts[key] = replace(
            tracked,
            # A task the tracker never synced: the latest move in the window says.
            tracker_state=tracked.tracker_state or moved_to,
            merged=tuple(merged.get(key, ())),
        )
    return facts


def _tracked_issue(key: str, node: GraphNode | None) -> IssueFact:
    """What the tracker last said of an issue, as the issue sync stored it on its task."""
    if node is None:
        return IssueFact(key=key, title=key, tracker_state=None, tracker_status=None)
    state = _metadata_str(node.metadata, "state")
    if state is None and _metadata_str(node.metadata, "status_category") == "done":
        state = "done"
    return IssueFact(
        key=key,
        title=node.name,
        tracker_state=state,
        tracker_status=_metadata_str(node.metadata, "status") if state is not None else None,
    )


def _issue_lines(issues: Mapping[str, IssueFact]) -> list[str]:
    done = [issue for issue in issues.values() if issue.done]
    merged_open = [issue for issue in issues.values() if issue.merged_ticket_open]
    other = [issue for issue in issues.values() if not issue.done and not issue.merged_ticket_open]
    lines: list[str] = []
    if done:
        lines.append(
            "Issues done in the tracker: "
            + "; ".join(f"{issue.key} {issue.title}" for issue in done[:_MAX_PER_SECTION])
            + "."
        )
    if merged_open:
        lines.append(
            "Merged, ticket still open in the tracker (not done): "
            + "; ".join(issue_sentence(issue).rstrip(".") for issue in merged_open)
            + "."
        )
    if other:
        lines.append(
            "Other issue updates (not done): "
            + "; ".join(
                f"{issue.key} {issue.title} ({issue.tracker_label} in the tracker)"
                for issue in other[:_MAX_PER_SECTION]
            )
            + "."
        )
    return lines


def issue_sentence(issue: IssueFact) -> str:
    """One true sentence about an issue: done only when the tracker says so."""
    if issue.done:
        return f"{issue.key} ({issue.title}) is done in the tracker."
    if issue.merged:
        requests = _join_and(issue.merged)
        return (
            f"{issue.key} ({issue.title}) is merged in {requests}, ticket still open "
            f"({issue.tracker_label} in the tracker)."
        )
    return f"{issue.key} ({issue.title}) is {issue.tracker_label} in the tracker, not done."


def _merge_facts(
    merge_requests: Mapping[tuple[str, str], PortfolioFeedItemView],
    mergers: Mapping[tuple[str, str], str],
    reader: _ScopeReader,
) -> tuple[MergeFact, ...]:
    return tuple(
        MergeFact(
            label=_merge_request_label(repo, number, item),
            number=number,
            repo=_repo_short_name(repo),
            keys=reader.named_keys(item.summary),
            merged=item.details.get("merged") is True,
            author=item.person_name,
            merger=mergers.get((repo, number)) or mergers.get((_repo_short_name(repo), number)),
        )
        for (repo, number), item in sorted(merge_requests.items())
    )


def _merge_request_lines(
    merge_requests: Mapping[tuple[str, str], PortfolioFeedItemView],
    merges: Sequence[MergeFact],
    as_of: datetime,
) -> list[str]:
    by_label = {merge.label: merge for merge in merges}
    merged: list[str] = []
    unmerged: list[str] = []
    for (repo, number), item in sorted(
        merge_requests.items(), key=lambda entry: entry[1].observed_at
    ):
        label = _merge_request_label(repo, number, item)
        title = _merge_request_title(item)
        author = f" opened by {item.person_name}" if item.person_name else ""
        when = _clock(item.observed_at, as_of)
        if item.details.get("merged") is True:
            merge = by_label.get(label)
            merger = merge.merger if merge is not None else None
            by = f", merged by {merger}" if merger else ", merged (who merged it is not recorded)"
            merged.append(f"{label} '{title}'{author}{by} at {when}")
        else:
            unmerged.append(f"{label} '{title}'{author}, not merged (updated {when})")
    lines: list[str] = []
    if merged:
        lines.append("Merge requests merged: " + "; ".join(merged[-_MAX_PER_SECTION:]) + ".")
    if unmerged:
        lines.append("Merge requests not merged: " + "; ".join(unmerged[-_MAX_PER_SECTION:]) + ".")
    return lines


def _checkin_lines(
    checkins: Mapping[str, Sequence[_Checkin]],
    names: Mapping[str, str],
    as_of: datetime,
    total: int,
) -> list[str]:
    if not checkins:
        return []
    latest = {person_id: history[-1] for person_id, history in checkins.items()}
    # Who checked in, by name. Counts by status word stay the status line's,
    # so the context never gives two different 'confirmed' counts.
    lines = [
        f"Checked in during the window ({len(latest)} of {max(total, len(latest))}): "
        + ", ".join(sorted(names.get(person_id) or "a team member" for person_id in latest))
        + "."
    ]
    unconfirmed = [
        f"{names.get(person_id) or 'a team member'} ({checkin.source}, {_clock(checkin.at, as_of)})"
        for person_id, checkin in sorted(latest.items(), key=lambda entry: names.get(entry[0], ""))
        if checkin.source != "confirmed"
    ]
    if unconfirmed:
        lines.append("Latest check-in not confirmed: " + "; ".join(unconfirmed) + ".")
    moves = [
        f"{names.get(person_id) or 'a team member'} {checkin.eta_change_days:+d} "
        f"{'day' if abs(checkin.eta_change_days) == 1 else 'days'} "
        f"(check-in at {_clock(checkin.at, as_of)}"
        f"{'' if checkin is history[-1] else ', not their latest'})"
        for person_id, history in sorted(
            checkins.items(), key=lambda entry: names.get(entry[0], "")
        )
        for checkin in history
        if checkin.eta_change_days
    ]
    if moves:
        lines.append("ETA changes reported: " + "; ".join(moves[:_MAX_PER_SECTION]) + ".")
    else:
        lines.append("ETA changes reported: none.")
    return lines


def _eta_changes(
    checkins: Mapping[str, Sequence[_Checkin]], names: Mapping[str, str]
) -> dict[str, tuple[EtaChange, ...]]:
    """Each named person's ETA changes of the window, oldest first."""
    changes: dict[str, tuple[EtaChange, ...]] = {}
    for person_id, history in checkins.items():
        name = names.get(person_id)
        moved = tuple(
            EtaChange(days=checkin.eta_change_days, at=checkin.at, latest=checkin is history[-1])
            for checkin in history
            if checkin.eta_change_days
        )
        if name and moved:
            changes[name] = moved
    return changes


@dataclass(frozen=True, kw_only=True)
class _BlockerFacts:
    #: What a brief says: each person's last report and what became of it.
    sentence: str
    #: The context's line: the same, with their earlier reports of the window.
    line: str
    reported: int
    open_now: int
    #: Everyone a blocker may be said to be of, by name.
    people: frozenset[str] = frozenset()


def _blocker_facts(
    inputs: BriefInputs,
    checkins: Mapping[str, Sequence[_Checkin]],
    names: Mapping[str, str],
    resolved_requests: Mapping[str, Sequence[datetime]],
    waits: Mapping[str, Sequence[str]],
) -> _BlockerFacts:
    """Blockers as the facts give them: reported, cleared and open now (N38).

    A person's last check-in of the window that reported blockers is stated
    with what became of them: still open, or cleared -- at the first later
    sign (a request of theirs resolved, a check-in with none) when there is
    one. Their earlier check-ins with blockers are listed too, so 'every
    check-in reported no blockers' cannot read as true.
    """
    known_open = inputs.open_blockers
    reporters = [
        (person_id, blocked)
        for person_id, history in sorted(
            checkins.items(), key=lambda entry: names.get(entry[0], "")
        )
        if (blocked := [checkin for checkin in history if checkin.blockers > 0])
    ]
    reports = [
        _person_blockers(
            names.get(person_id) or "a team member",
            blocked,
            checkins[person_id],
            None if known_open is None else known_open.get(person_id, 0),
            resolved_requests.get(person_id, ()),
            inputs.as_of,
            waits.get(person_id, ()),
        )
        for person_id, blocked in reporters
    ]
    people = frozenset(
        name
        for person_id in {person for person, _ in reporters}
        | {person for person, count in (known_open or {}).items() if count}
        if (name := names.get(person_id) or inputs.people.get(person_id))
    )
    open_total = sum((known_open or {}).values())
    open_people = [
        f"{names.get(person_id) or inputs.people.get(person_id) or 'a team member'} {count}"
        for person_id, count in sorted((known_open or {}).items())
        if count
    ]
    if known_open is None:
        now = ""
    elif open_total:
        now = f"Open now: {', '.join(open_people)}."
    else:
        now = "No blocker is open now."
    if reports:
        said = "; ".join(text for text, _ in reports) + "."
        listed = "; ".join(text + earlier for text, earlier in reports) + "."
        # Said outright, so a blocker is never read as anyone else's (N51).
        nobody_else = "Nobody else reported a blocker in the window."
        return _BlockerFacts(
            sentence=f"{said} {now}".strip(),
            line=f"{listed} {now} {nobody_else}".replace("  ", " ").strip(),
            reported=len(reports),
            open_now=open_total,
            people=people,
        )
    if known_open is not None and not open_total:
        sentence = "No check-in in the window reported a blocker, and none is open now."
    else:
        sentence = f"No check-in in the window reported a blocker. {now}".strip()
    return _BlockerFacts(
        sentence=sentence, line=sentence, reported=0, open_now=open_total, people=people
    )


def _person_blockers(
    name: str,
    blocked: Sequence[_Checkin],
    history: Sequence[_Checkin],
    open_now: int | None,
    resolutions: Sequence[datetime],
    as_of: datetime,
    waits: Sequence[str] = (),
) -> tuple[str, str]:
    """A person's last report of the window and what became of it, and their earlier ones."""
    last = blocked[-1]
    waiting = f" (waiting on {_join_and(waits)})" if waits else ""
    text = (
        f"{name}'s check-in at {_clock(last.at, as_of)} reported "
        f"{_count(last.blockers, ('blocker', 'blockers'))}{waiting}"
    )
    if open_now:
        text += f", {open_now} still open"
    elif open_now == 0:
        cleared_at = _cleared_at(last, history, resolutions)
        text += f", cleared at {_clock(cleared_at, as_of)}" if cleared_at else ", cleared since"
    earlier = blocked[:-1][-_MAX_EARLIER_BLOCKED:]
    if not earlier:
        return text, ""
    listed = ", ".join(f"{checkin.blockers} at {_clock(checkin.at, as_of)}" for checkin in earlier)
    return text, f" (earlier in the window: {listed})"


def _people_in_scope(inputs: BriefInputs) -> int:
    members = inputs.scope.member_ids
    if members is not None:
        return len(members)
    return sum(1 for person_id, name in inputs.people.items() if _usable_person(name, person_id))


def _cleared_at(
    last: _Checkin,
    history: Sequence[_Checkin],
    resolutions: Sequence[datetime],
) -> datetime | None:
    signs = [checkin.at for checkin in history if checkin.at > last.at and checkin.blockers == 0]
    signs.extend(at for at in resolutions if at > last.at)
    return min(signs) if signs else None


def _request_lines(
    requests: Mapping[str, _Request], as_of: datetime, merges: Sequence[MergeFact] = ()
) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for request in sorted(requests.values(), key=lambda entry: entry.item.observed_at):
        details = request.item.details
        kind_value = _detail_str(details, "dependency_kind") or "request"
        kind = _REQUEST_KIND_LABELS.get(kind_value, kind_value.replace("_", " "))
        reporter = _detail_str(details, "reporter_name") or "someone"
        counterpart = _detail_str(details, "referenced_person_name") or "an unresolved counterpart"
        summary = _detail_str(details, "summary")
        text = f"{reporter}'s {kind} request to {counterpart}"
        if summary:
            text += f' ("{summary}")'
            meant = resolve_bare_merge_request(summary, {reporter, counterpart}, merges)
            if meant is not None:
                text += f", that is {meant.named}"
        if request.transition == "resolved":
            text += f", {_clock(request.item.observed_at, as_of)}"
        state = request.transition if request.transition in _REQUEST_STATE_LABELS else "opened"
        grouped.setdefault(state, []).append(text)
    return [
        f"{_REQUEST_STATE_LABELS[state]}: " + "; ".join(grouped[state][-_MAX_PER_SECTION:]) + "."
        for state in _REQUEST_STATE_ORDER
        if state in grouped
    ]


def resolve_bare_merge_request(
    text: str, people: set[str], merges: Sequence[MergeFact]
) -> MergeFact | None:
    """The one merge request a text names by number alone ("merge !1"), if it can be told.

    Of the window's merge requests with that number, the one the text's people
    opened or merged; with none of theirs, the only one with that number.
    """
    match = BARE_MERGE_REQUEST.search(text)
    if match is None:
        return None
    before = text[: match.start()].rstrip().rsplit(" ", 1)[-1].casefold()
    if before and any(before == merge.repo for merge in merges):
        return None
    numbered = [merge for merge in merges if merge.number == match.group(1)]
    theirs = [merge for merge in numbered if {merge.author, merge.merger} & people]
    candidates = theirs or numbered
    return candidates[0] if len(candidates) == 1 else None


def _action_lines(
    inputs: BriefInputs,
    collected: _Collected,
    issues: Mapping[str, IssueFact],
    merges: Sequence[MergeFact],
) -> tuple[str, ...]:
    """Who needs to act on what today, as short sentences built from the facts only."""
    lines: list[str] = []
    day = inputs.checkins_today or ()
    silent = sum(1 for checkin in day if not checkin.answered)
    if day and silent:
        lines.append(
            f"{silent} of {_count(len(day), ('team member', 'team members'))} "
            "have not answered the day's check-in."
        )
    for request in collected.requests.values():
        if request.transition != "needs_resolution":
            continue
        details = request.item.details
        reporter = _detail_str(details, "reporter_name") or "Someone"
        kind_value = _detail_str(details, "dependency_kind") or "request"
        kind = _REQUEST_KIND_LABELS.get(kind_value, kind_value.replace("_", " "))
        meant = resolve_bare_merge_request(
            _detail_str(details, "summary") or "", {reporter}, merges
        )
        about = f" for {meant.named}" if meant is not None else ""
        lines.append(f"{reporter}'s {kind} request{about} needs a PM to resolve it.")
    for person_id, count in sorted((inputs.open_blockers or {}).items()):
        name = inputs.people.get(person_id)
        if count and name:
            lines.append(f"{name} has {_count(count, ('open blocker', 'open blockers'))}.")
    lines.extend(
        f"{issue.key} is merged but still open in the tracker."
        for issue in issues.values()
        if issue.merged_ticket_open
    )
    return tuple(lines)


def _risk_lines(risks: Mapping[tuple[str, str], PortfolioFeedItemView]) -> list[str]:
    opened = [item.summary for item in risks.values() if item.kind != "risk_cleared"]
    cleared = [item.summary for item in risks.values() if item.kind == "risk_cleared"]
    lines: list[str] = []
    if opened:
        lines.append("Signals: " + "; ".join(opened[:_MAX_PER_SECTION]) + ".")
    if cleared:
        lines.append("Signals cleared: " + "; ".join(cleared[:_MAX_PER_SECTION]) + ".")
    return lines


def _work_item_lines(work_items: Mapping[str, PortfolioFeedItemView]) -> list[str]:
    if not work_items:
        return []
    return [
        "Work items: "
        + "; ".join(item.summary for item in list(work_items.values())[:_MAX_PER_SECTION])
        + "."
    ]


def _commit_lines(commits: Mapping[str, int]) -> list[str]:
    if not commits:
        return []
    return [
        "Commits: " + "; ".join(f"{repo} {count}" for repo, count in sorted(commits.items())) + "."
    ]


def _merged_request(summary: str, repo: str) -> tuple[str, str] | None:
    """The (repo, number) a merge commit's message names, if it is one."""
    if (match := _GITLAB_MERGE.search(summary)) is not None:
        return match.group("repo"), match.group("id")
    if (match := _GITHUB_MERGE.search(summary)) is not None:
        return repo, match.group("id")
    return None


def _merge_request_label(repo: str, number: str, item: PortfolioFeedItemView | None = None) -> str:
    """How a person names a merge request: "insights-pipeline !1" (GitLab), "api #4" (GitHub).

    Its web address says which host writes which; without one, it is said
    provider-neutrally, "merge request 1 in insights-pipeline".
    """
    short = repo.rstrip("/").rsplit("/", 1)[-1]
    url = (_detail_str(item.details, "web_url") or "") if item is not None else ""
    if "/merge_requests/" in url:
        return f"{short} !{number}"
    if "/pull/" in url:
        return f"{short} #{number}"
    return f"merge request {number} in {short}"


def _merge_request_title(item: PortfolioFeedItemView) -> str:
    # The feed's summary is "PR <id> in <repo> <merged|updated>: <title>".
    _, _, title = item.summary.partition(": ")
    return title.strip() or item.summary


def _sources(scope_ref: str | None, cited: Iterable[str]) -> tuple[str, ...]:
    sources: list[str] = []
    seen: set[str] = set()
    for ref in ([scope_ref] if scope_ref else []) + list(cited):
        if ref and ref not in seen:
            seen.add(ref)
            sources.append(ref)
        if len(sources) >= _MAX_SOURCES:
            break
    return tuple(sources)


def _ref(item: PortfolioFeedItemView) -> str:
    return f"{item.entity_ref.kind.value}:{item.entity_ref.id}"


def _key_order(key: str) -> tuple[str, int]:
    """CHK-3 before CHK-13."""
    prefix, _, number = key.rpartition("-")
    return prefix, int(number) if number.isdigit() else 0


def _fact_keys(text: str) -> set[str]:
    return {match.upper() for match in _FACT_ISSUE_KEY.findall(text)}


def _clock(at: datetime, as_of: datetime) -> str:
    """'06:06 UTC' on the brief's own day, '3 Oct 18:03 UTC' before it."""
    moment = at.astimezone(UTC) if at.tzinfo is not None else at.replace(tzinfo=UTC)
    today = (as_of.astimezone(UTC) if as_of.tzinfo is not None else as_of).date()
    if moment.date() == today:
        return f"{moment:%H:%M} UTC"
    return f"{moment.day} {moment:%b %H:%M} UTC"


def _kind_count(kind: NodeKind, count: int) -> str:
    if kind is NodeKind.PROGRAM and count == 1:
        return "the program"
    return _count(count, _KIND_NOUNS[kind])


def _count(count: int, nouns: tuple[str, str]) -> str:
    return f"{count} {nouns[0] if count == 1 else nouns[1]}"


def _join_and(parts: Iterable[str]) -> str:
    items = list(parts)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _repo_short_name(repo: str) -> str:
    return repo.rstrip("/").rsplit("/", 1)[-1].casefold()


def _usable_person(name: str, person_id: str) -> bool:
    cleaned = name.strip()
    return bool(cleaned) and cleaned != person_id


def _detail_str(details: Mapping[str, JsonScalar], key: str) -> str | None:
    value = details.get(key)
    return value if isinstance(value, str) and value else None


def _detail_int(details: Mapping[str, JsonScalar], key: str) -> int | None:
    value = details.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _metadata_str(metadata: Mapping[str, JsonScalar], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
