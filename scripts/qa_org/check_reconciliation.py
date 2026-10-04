"""Check that Slack, Jira, GitLab and OpenProgram agree about the QA org on one day.

    uv run python -m scripts.qa_org.check_reconciliation                     # today (UTC)
    uv run python -m scripts.qa_org.check_reconciliation --date 2026-10-05
    uv run python -m scripts.qa_org.check_reconciliation --out PATH

Everything is read, never written: Jira and GitLab through their REST APIs,
Slack through the bot's own DM history (``im:history``), OpenProgram through its
API plus ``SELECT``s on the shared Postgres. It reports, per issue and per
person, where two systems disagree, and the reconciliation a reader would
expect (a merged MR on an open issue, a check-in claiming done on an open
issue, ...) next to what OpenProgram flagged itself. A person's statement that
names the state Jira shows ("not started" on a To Do issue) is agreement, and so
is one that puts an In Progress issue in review ("pending review") while its
merge request is open: Jira has no review state.

The report names real people, workspace URLs and account ids, so it goes next to
QA-ORG.md outside the repository, which is public. It never contains a token.
Each run writes a new ``RECONCILIATION-<date>-<HHMM>.md`` (UTC time of the run),
so earlier reports are kept; ``--out PATH`` writes exactly that file instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import GITLAB_GROUP, PODS, PROJECTS, QA_TENANT
from scripts.qa_org.write_setup_doc import API, GITLAB_WEB, table

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT.parent / "openprogram-qa"
TENANT = QA_TENANT
POSTGRES = ("docker", "exec", "-i", "openprogram-postgres-1", "psql", "-U", "openprogram")
JIRA_KEY = re.compile(r"\b(?:CHK|IDP|INS)-\d+\b")
# Jira status category keys: "new" is To Do, "indeterminate" In Progress.
TODO = "new"
ACTIVE = "indeterminate"
DONE = "done"
# What OpenProgram stores for a person who has not replied. It is not a blocker.
NO_REPLY = "no confirmed reply"
# A person's own words for an issue's state, by the Jira category they name. Only a whole
# statement counts ("not started", "in progress"); wording that merely mentions a state
# ("starting", "on track", "waiting for review") is not agreement, so it stays on the list.
SAID_AS_CATEGORY: dict[str, str] = {
    **dict.fromkeys(
        (
            "to do",
            "todo",
            "backlog",
            "open",
            "new",
            "not started",
            "not started yet",
            "not yet started",
            "yet to start",
            "hasn t started",
            "haven t started",
            "not begun",
        ),
        TODO,
    ),
    **dict.fromkeys(
        (
            "in progress",
            "inprogress",
            "wip",
            "work in progress",
            "ongoing",
            "underway",
            "in development",
            "started",
            "doing",
        ),
        ACTIVE,
    ),
}
# The review stage, as people put it. Jira has no review state (N14): an issue under
# review is In Progress there, and its open merge request is what is being reviewed. So
# such a statement agrees with In Progress only while the issue has an open merge request
# that is not a draft; with none (the product asks for one, R1-10), or on a To Do or Done
# issue, it stays on the list. Whole statements only, as above, but one may end by saying
# when ("should be approved and merged by end of week").
SAID_IN_REVIEW = frozenset(
    {
        "in review",
        "in code review",
        "under review",
        "up for review",
        "ready for review",
        "pending review",
        "review pending",
        "awaiting review",
        "waiting for review",
        "waiting on review",
        "code complete pending review",
        "pending approval",
        "awaiting approval",
        "approved awaiting merge",
        "ready to merge",
        "should be approved and merged",
        "mr open",
        "mr opened",
    }
)
# When a review statement says it should be over: "... by end of week", "... today".
REVIEW_WHEN = re.compile(
    r" (?:(?:by|on|before|until) )?(?:(?:early|late) )?(?:the )?"
    r"(?:end of (?:the )?(?:day|week|sprint)|eod|eow|today|tonight|tomorrow|this week|next week"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day)$"
)
NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass
class Findings:
    """Disagreements, grouped by the section that found them."""

    items: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add(self, section: str, text: str) -> None:
        self.items[section].append(text)

    def count(self) -> int:
        return sum(len(v) for v in self.items.values())


def sql(query: str) -> list[dict[str, Any]]:
    """Run one read-only query and return its rows as dicts."""
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({query}) t"
    result = subprocess.run(
        [*POSTGRES, "-d", "openprogram", "-At", "-c", wrapped],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"postgres query failed: {result.stderr.strip()}")
    return list(json.loads(result.stdout or "[]"))


def jira_issues() -> list[dict[str, Any]]:
    base, email, token = env.require(
        "OPENPROGRAM_JIRA_BASE_URL", "OPENPROGRAM_JIRA_EMAIL", "OPENPROGRAM_JIRA_API_TOKEN"
    ).values()
    keys = ", ".join(p.jira_key for p in PROJECTS)
    with httpx.Client(base_url=base.rstrip("/"), auth=(email, token), timeout=30) as client:
        found = client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": f"project in ({keys}) ORDER BY key ASC",
                "fields": ["summary", "status", "assignee", "components", "labels"],
                "maxResults": 200,
            },
        )
        found.raise_for_status()
    return list(found.json().get("issues", []))


def gitlab_mrs() -> list[dict[str, Any]]:
    token = env.require("OPENPROGRAM_GITLAB_TOKEN")["OPENPROGRAM_GITLAB_TOKEN"]
    with httpx.Client(
        base_url=f"{GITLAB_WEB}/api/v4", headers={"PRIVATE-TOKEN": token}, timeout=30
    ) as client:
        response = client.get(
            f"/groups/{GITLAB_GROUP}/merge_requests", params={"state": "all", "per_page": "100"}
        )
        response.raise_for_status()
    return list(response.json())


def slack_history(channel: str, day: date) -> list[dict[str, Any]]:
    token = env.require("OPENPROGRAM_SLACK_BOT_TOKEN")["OPENPROGRAM_SLACK_BOT_TOKEN"]
    start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
    response = httpx.get(
        "https://slack.com/api/conversations.history",
        params={"channel": channel, "oldest": str(start), "latest": str(start + 86400 * 2)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    ).json()
    if not response.get("ok"):
        return [{"ts": "0", "text": f"(history unavailable: {response.get('error')})"}]
    return sorted(response.get("messages", []), key=lambda m: float(m["ts"]))


def api_get(path: str, **params: str) -> Any:  # noqa: ANN401
    response = httpx.get(f"{API}{path}", params=params, timeout=30)
    return response.json() if response.status_code == 200 else None


def expected_pods(project_key: str, components: set[str], labels: set[str]) -> set[str]:
    """Pods whose roster scope covers an issue: whole project, a component, or a label."""
    project_id = next(p.id for p in PROJECTS if p.jira_key == project_key)
    pods: set[str] = set()
    for pod in PODS:
        if project_id not in pod.project_ids:
            continue
        scope = pod.jira_filter_jql
        if scope is None:
            pods.add(pod.id)
        elif scope.startswith("component = ") and scope.removeprefix("component = ") in components:
            pods.add(pod.id)
        elif scope.startswith("labels = ") and scope.removeprefix("labels = ") in labels:
            pods.add(pod.id)
    return pods


def says_state_of(said: str, category: str) -> bool:
    """True when a person's whole statement names the state Jira shows for the issue."""
    return SAID_AS_CATEGORY.get(NON_WORD.sub(" ", said.lower()).strip()) == category


def says_in_review(said: str) -> bool:
    """True when a person's whole statement puts the issue in review, perhaps saying until when."""
    words = NON_WORD.sub(" ", said.lower()).strip()
    return words in SAID_IN_REVIEW or REVIEW_WHEN.sub("", words) in SAID_IN_REVIEW


def agrees_with(said: str, category: str, *, open_mr: bool) -> bool:
    """True when a statement agrees with Jira: it names Jira's state, or, for an In Progress
    issue whose merge request is open and not a draft, it puts the issue in review."""
    if says_state_of(said, category):
        return True
    return category == ACTIVE and open_mr and says_in_review(said)


def blocker_count(blockers: dict[str, Any] | list[Any] | None) -> int:
    """Real blockers in a developer_statuses row.

    The column holds ``{"items": [text, ...]}``, so ``len()`` of the object is 1 (its one
    key) for everyone, with or without blockers. The placeholder stored for silence is
    not a blocker either.
    """
    items = blockers.get("items") if isinstance(blockers, dict) else blockers
    return sum(1 for item in items or [] if str(item).strip().lower() != NO_REPLY)


def default_out(day: date, now: datetime) -> Path:
    """A new report file for this run: ``RECONCILIATION-<day>-<HHMM>.md``, HHMM in UTC.

    A report is never replaced by the default name: a second run in the same minute gets
    ``-2``, ``-3``, ... after the time.
    """
    stem = f"RECONCILIATION-{day.isoformat()}-{now:%H%M}"
    path = OUT_DIR / f"{stem}.md"
    taken = 1
    while path.exists():
        taken += 1
        path = OUT_DIR / f"{stem}-{taken}.md"
    return path


def _hhmm(ts: str | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=UTC).strftime("%H:%M:%S")


def _clip(text: object, limit: int = 160) -> str:
    flat = " ".join(str(text or "").split()).replace("|", "/")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@dataclass(frozen=True)
class TaskGraph:
    """OpenProgram's view of the Jira issues: task nodes, assignees, pods, Jira links."""

    tasks: dict[str, dict[str, Any]]
    assignees: dict[str, set[str]]
    pods: dict[str, set[str]]
    account_to_member: dict[str, str]


def task_graph() -> TaskGraph:
    tasks = {
        row["id"]: row
        for row in sql(
            f"select id, metadata from graph_nodes where tenant_id='{TENANT}' and kind='task'"
        )
    }
    edges = sql(
        "select e.kind, e.from_node_id, e.to_node_id, f.kind as from_kind from graph_edges e "
        "join graph_nodes f on f.tenant_id=e.tenant_id and f.id=e.from_node_id "
        f"where e.tenant_id='{TENANT}' and e.valid_to is null "
        "and e.to_node_id in (select id from graph_nodes "
        f"where tenant_id='{TENANT}' and kind='task')"
    )
    assignees: dict[str, set[str]] = defaultdict(set)
    pods: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge["kind"] == "assigned_to" and edge["from_kind"] == "developer":
            assignees[edge["to_node_id"]].add(edge["from_node_id"])
        if edge["kind"] == "contains" and edge["from_kind"] == "pod":
            pods[edge["to_node_id"]].add(edge["from_node_id"])
    account_to_member = {
        row["jira_account_id"]: row["developer_id"]
        for row in sql(
            "select developer_id, jira_account_id from identity_links "
            f"where tenant_id='{TENANT}' and jira_account_id is not null"
        )
    }
    return TaskGraph(tasks, assignees, pods, account_to_member)


def _issue_notes(
    issue: dict[str, Any], graph: TaskGraph, names: dict[str, str]
) -> tuple[str | None, list[str]]:
    """The member Jira assigns the issue to, and every way OpenProgram disagrees."""
    key = issue["key"]
    fields = issue["fields"]
    status = fields["status"]["name"]
    account = (fields.get("assignee") or {}).get("accountId")
    want_member = graph.account_to_member.get(account) if account else None
    want_pods = expected_pods(
        key.split("-")[0],
        {c["name"] for c in fields.get("components") or []},
        set(fields.get("labels") or []),
    )
    node = graph.tasks.get(key)
    got_status = (node or {}).get("metadata", {}).get("status")
    got_members = graph.assignees.get(key, set())
    got_pods = graph.pods.get(key, set())
    notes = []
    if node is None:
        notes.append("no task node")
    elif got_status != status:
        notes.append(f"status: Jira {status}, OpenProgram {got_status}")
    if want_member and want_member not in got_members:
        notes.append(f"assignee {names.get(want_member, want_member)} not linked")
    if account and not want_member:
        notes.append("Jira assignee has no linked member")
    stale = got_members - ({want_member} if want_member else set())
    if stale:
        notes.append("extra assignee " + ", ".join(names.get(m, m) for m in sorted(stale)))
    if want_pods != got_pods:
        notes.append(
            f"pods: roster {', '.join(sorted(want_pods)) or '—'}; "
            f"OpenProgram {', '.join(sorted(got_pods)) or '—'}"
        )
    return want_member, notes


def section_jira(issues: list[dict[str, Any]], names: dict[str, str], found: Findings) -> list[str]:
    graph = task_graph()
    rows = []
    for issue in issues:
        key = issue["key"]
        fields = issue["fields"]
        want_member, notes = _issue_notes(issue, graph, names)
        for note in notes:
            found.add("Jira ↔ OpenProgram", f"{key}: {note}")
        got_status = graph.tasks.get(key, {}).get("metadata", {}).get("status")
        rows.append(
            [
                key,
                fields["status"]["name"],
                got_status or "—",
                names.get(want_member or "", (fields.get("assignee") or {}).get("displayName"))
                or "unassigned",
                ", ".join(names.get(m, m) for m in sorted(graph.assignees.get(key, set()))) or "—",
                "; ".join(notes) or "ok",
            ]
        )
    header = ["Key", "Jira status", "OpenProgram status", "Jira assignee", "OP assignee", "Check"]
    return ["## Jira ↔ OpenProgram", "", *table(header, rows), ""]


def latest_mr_facts() -> dict[tuple[str, str], dict[str, Any]]:
    facts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sql(
        "select entity_id, payload, observed_at from facts "
        f"where tenant_id='{TENANT}' and source='vcs_pull_request' order by observed_at"
    ):
        payload = row["payload"]
        facts[(payload["repo"], str(payload["id"]))] = {**payload, "developer": row["entity_id"]}
    return facts


def section_gitlab(
    mrs: list[dict[str, Any]], names: dict[str, str], found: Findings
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    facts = latest_mr_facts()
    by_username = {
        row["vcs_username"]: row["developer_id"]
        for row in sql(
            "select developer_id, vcs_username from identity_links "
            f"where tenant_id='{TENANT}' and vcs_username is not null"
        )
    }
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = []
    for mr in sorted(mrs, key=lambda m: m["references"]["full"]):
        repo = mr["references"]["full"].split("!")[0]
        ref = mr["references"]["full"]
        keys = set(JIRA_KEY.findall(mr["source_branch"] + " " + mr["title"]))
        match = JIRA_KEY.search(mr["source_branch"] + " " + mr["title"])
        key = match.group(0) if match else None
        state = "draft" if mr.get("draft") and mr["state"] == "opened" else mr["state"]
        if key:
            by_key[key].append({"ref": ref, "state": state})
        fact = facts.get((repo, str(mr["iid"])))
        want_dev = by_username.get(mr["author"]["username"])
        notes = []
        if fact is None:
            notes.append("no vcs_pull_request fact")
        else:
            if bool(fact.get("merged")) != (mr["state"] == "merged"):
                notes.append(f"merged: GitLab {mr['state']}, OpenProgram {fact.get('merged')}")
            if want_dev and fact["developer"] != want_dev:
                notes.append(f"author on {names.get(fact['developer'], fact['developer'])}")
            notes += _state_notes(mr, fact)
        if not keys:
            notes.append("no Jira key in branch or title")
        for note in notes:
            found.add("GitLab ↔ OpenProgram", f"{ref}: {note}")
        rows.append(
            [
                ref,
                state,
                mr["author"]["username"],
                key or "—",
                "—" if fact is None else ("merged" if fact.get("merged") else "open"),
                "; ".join(notes) or "ok",
            ]
        )
    header = ["MR", "GitLab state", "Author", "Jira", "OpenProgram", "Check"]
    return ["## GitLab ↔ OpenProgram", "", *table(header, rows), ""], by_key


def _state_notes(mr: dict[str, Any], fact: dict[str, Any]) -> list[str]:
    """How the stored state differs from GitLab's: state, draft flag, branch."""
    want_state = {"opened": "open", "locked": "open"}.get(mr["state"], mr["state"])
    want_draft = bool(mr.get("draft"))
    if "state" not in fact:
        # Recorded before MR state was stored; refreshed when the MR next changes.
        if want_state == "closed" or want_draft:
            return [f"{'draft' if want_draft else want_state} stored without state (old fact)"]
        return []
    notes = []
    if fact["state"] != want_state:
        notes.append(f"state: GitLab {want_state}, OpenProgram {fact['state']}")
    if bool(fact.get("draft")) != want_draft:
        notes.append(f"draft: GitLab {want_draft}, OpenProgram {bool(fact.get('draft'))}")
    if fact.get("source_branch") not in {None, mr["source_branch"]}:
        notes.append(f"branch: GitLab {mr['source_branch']}, OpenProgram {fact['source_branch']}")
    return notes


def section_reconcile(
    issues: list[dict[str, Any]],
    mrs_by_key: dict[str, list[dict[str, Any]]],
    claims: dict[str, list[dict[str, Any]]],
    flagged: dict[str, list[str]],
    found: Findings,
) -> list[str]:
    """What a reader would expect to be flagged, next to what OpenProgram flagged."""
    rows = []
    for issue in issues:
        key = issue["key"]
        category = issue["fields"]["status"]["statusCategory"]["key"]
        status = issue["fields"]["status"]["name"]
        mrs = mrs_by_key.get(key, [])
        merged = any(m["state"] == "merged" for m in mrs)
        open_mr = any(m["state"] in {"opened", "draft"} for m in mrs)
        # What a review statement needs: an open merge request ready for review.
        in_review = any(m["state"] == "opened" for m in mrs)
        expected = []
        if merged and category != DONE:
            expected.append("MR merged, issue not done")
        if category == DONE and open_mr and not merged:
            expected.append("issue done, MR still open")
        if category == "indeterminate" and not mrs:
            expected.append("in progress with no MR")
        for claim in claims.get(key, []):
            said = claim.get("claimed_state") or ("done" if claim.get("claimed_done") else None)
            if said and claim.get("claimed_done") and category != DONE:
                expected.append(f"{claim['who']} said done, Jira {status}")
            elif (
                said
                and not claim.get("claimed_done")
                and not agrees_with(said, category, open_mr=in_review)
            ):
                expected.append(f"{claim['who']} said {said}, Jira {status}")
        if not expected and key not in flagged:
            continue
        got = flagged.get(key, [])
        if expected and not got:
            found.add("Reconciliation", f"{key}: expected '{'; '.join(expected)}', nothing flagged")
        rows.append(
            [
                key,
                status,
                ", ".join(f"{m['ref']} {m['state']}" for m in mrs) or "—",
                "; ".join(expected) or "—",
                "; ".join(got) or "nothing",
            ]
        )
    header = ["Key", "Jira", "MRs", "Expected to be flagged", "OpenProgram flagged"]
    return ["## Reconciliation", "", *table(header, rows), ""]


def openprogram_flags(day: date) -> dict[str, list[str]]:
    """Risk and drift findings per Jira key mentioned anywhere in their evidence."""
    flags: dict[str, list[str]] = defaultdict(list)
    for project in PROJECTS:
        body = api_get(f"/projects/{project.id}/risks", as_of=day.isoformat()) or {}
        for finding in [*body.get("risks", []), *body.get("drift", [])]:
            text = json.dumps(finding)
            label = str(finding.get("rule_id") or finding.get("kind") or "finding")
            for key in set(JIRA_KEY.findall(text)):
                flags[key].append(label)
    return flags


def checkin_rows(day: date) -> list[dict[str, Any]]:
    iso = day.isoformat()
    return sql(
        "select n.id as developer_id, n.name, r.status as run_status, r.reason, "
        "c.chat_thread_ref, c.asked_at, c.consumed_at, k.replied_at, k.raw_reply, k.signals, "
        "s.source, s.summary, s.blockers, s.eta_change_days "
        "from graph_nodes n "
        "left join checkin_schedule_runs r on r.tenant_id=n.tenant_id "
        f"  and r.developer_id=n.id and r.checkin_date='{iso}' "
        "left join checkin_correlations c on c.tenant_id=n.tenant_id "
        "  and c.correlation_id=r.correlation_id "
        "left join checkins k on k.tenant_id=n.tenant_id and k.correlation_id=r.correlation_id "
        "left join developer_statuses s on s.tenant_id=n.tenant_id "
        f"  and s.developer_id=n.id and s.as_of='{iso}' "
        f"where n.tenant_id='{TENANT}' and n.kind='developer' order by n.name"
    )


def _flag_person(
    row: dict[str, Any], replied_in_slack: bool, events: dict[str, Any], iso: str, found: Findings
) -> None:
    who = row["name"]
    if replied_in_slack and row["replied_at"] is None:
        found.add("Slack ↔ OpenProgram", f"{who}: replied in Slack, no check-in recorded")
    if events and events["n"] != events["done"]:
        found.add(
            "Slack ↔ OpenProgram",
            f"{who}: {events['n'] - events['done']} inbound event(s) not processed",
        )
    if not row["developer_id"].startswith("U"):
        found.add("Check-in dispatch", f"{who} ({row['developer_id']}) is a developer node")
    elif row["run_status"] is None:
        found.add("Check-in dispatch", f"{who}: no check-in run for {iso}")


def _transcript(who: str, history: list[dict[str, Any]]) -> list[str]:
    """One person's DM for the day, oldest first, thread replies marked."""
    if not history:
        return []
    lines = [f"### {who}", ""]
    for message in history:
        speaker = "bot" if message.get("bot_id") else who.split()[0]
        threaded = message.get("thread_ts") not in {None, message["ts"]}
        text = _clip(message.get("text"), 400)
        lines.append(
            f"- {_hhmm(message['ts'])} **{speaker}**{' (thread)' if threaded else ''}: {text}"
        )
    return [*lines, ""]


def section_checkins(
    day: date, found: Findings
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    iso = day.isoformat()
    events = sql(
        "select chat_user_ref, count(*) as n, count(processed_at) as done from inbound_chat_events "
        f"where tenant_id='{TENANT}' and received_at::date='{iso}' group by 1"
    )
    inbound = {row["chat_user_ref"]: row for row in events}
    audits = sql(
        "select developer_id, issue_key, status, target_state, before_state, after_state "
        f"from writeback_audit where tenant_id='{TENANT}' and created_at::date='{iso}' "
        "order by created_at"
    )
    requests = sql(
        "select requester_id, counterpart_id, kind, status, note, notify_message_id "
        f"from cross_person_requests where tenant_id='{TENANT}' and created_at::date='{iso}'"
    )
    claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
    lines = [f"## Check-ins on {iso}", ""]
    summary = []
    for row in checkin_rows(day):
        who = row["name"]
        signals = row["signals"] or {}
        for update in signals.get("issue_updates") or []:
            claims[str(update.get("issue_key"))].append({**update, "who": who})
        history = slack_history(row["chat_thread_ref"], day) if row["chat_thread_ref"] else []
        user_msgs = [m for m in history if not m.get("bot_id")]
        events_row = inbound.get(row["developer_id"], {})
        _flag_person(row, bool(user_msgs), events_row, iso, found)
        summary.append(
            [
                who,
                row["run_status"] or "—",
                len([m for m in history if m.get("bot_id")]),
                len(user_msgs),
                f"{events_row.get('done', 0)}/{events_row.get('n', 0)}",
                row["source"] or "—",
                blocker_count(row["blockers"]),
                _clip(row["summary"], 90),
            ]
        )
        lines += _transcript(who, history)
    header = ["Person", "Run", "Bot msgs", "Replies", "Events", "Status", "Blockers", "Summary"]
    lines[2:2] = [*table(header, summary), ""]
    if audits:
        lines += ["### Write-back", ""]
        lines += table(
            ["Who", "Issue", "Status", "Target", "Before", "After"],
            [
                [
                    a["developer_id"],
                    a["issue_key"],
                    a["status"],
                    a["target_state"],
                    a["before_state"],
                    a["after_state"],
                ]
                for a in audits
            ],
        )
        lines.append("")
    if requests:
        lines += ["### Cross-person requests", ""]
        lines += table(
            ["From", "To", "Kind", "Status", "Notified", "Note"],
            [
                [
                    r["requester_id"],
                    r["counterpart_id"] or "—",
                    r["kind"],
                    r["status"],
                    "yes" if r["notify_message_id"] else "no",
                    _clip(r["note"], 80),
                ]
                for r in requests
            ],
        )
        lines.append("")
    return lines, claims


def section_rollups(day: date) -> list[str]:
    rows = []
    for pod in PODS:
        body = api_get(f"/pods/{pod.id}/rollup", as_of=day.isoformat()) or {}
        factors = body.get("factors", [])
        counts: dict[str, int] = defaultdict(int)
        for factor in factors:
            counts[factor["contributes"]] += 1
        mix = ", ".join(f"{n} {rag}" for rag, n in sorted(counts.items()))
        rows.append([pod.name, body.get("rag", "—"), mix or "—"])
    for project in PROJECTS:
        body = api_get(f"/projects/{project.id}/progress", as_of=day.isoformat()) or {}
        rows.append(
            [project.name, body.get("rag", "—"), f"{body.get('percent_complete', '—')}% complete"]
        )
    return ["## Rollups", "", *table(["Node", "RAG", "Factors"], rows), ""]


def render(day: date, generated: datetime) -> tuple[str, Findings]:
    found = Findings()
    names = {
        row["id"]: row["name"]
        for row in sql(
            f"select id, name from graph_nodes where tenant_id='{TENANT}' and kind='developer'"
        )
    }
    issues = jira_issues()
    checkins, claims = section_checkins(day, found)
    jira = section_jira(issues, names, found)
    gitlab, mrs_by_key = section_gitlab(gitlab_mrs(), names, found)
    reconcile = section_reconcile(issues, mrs_by_key, claims, openprogram_flags(day), found)
    rollups = section_rollups(day)
    head = [
        f"# QA org reconciliation, {day.isoformat()}",
        "",
        f"Generated {generated:%Y-%m-%d %H:%M} UTC by "
        "`uv run python -m scripts.qa_org.check_reconciliation`. Read-only. **Private.**",
        "",
        f"## {found.count()} disagreement(s)",
        "",
    ]
    for section, texts in found.items.items():
        head += [f"**{section}**", "", *(f"- {t}" for t in texts), ""]
    return "\n".join([*head, *checkins, *reconcile, *jira, *gitlab, *rollups]), found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help="write the report to exactly this file, replacing it if it exists; keep it outside "
        "the repository (default: a new RECONCILIATION-<date>-<HHMM>.md next to QA-ORG.md)",
    )
    args = parser.parse_args()
    day: date = args.date
    started = datetime.now(UTC)
    out: Path = args.out.expanduser() if args.out else default_out(day, started)
    text, found = render(day, started)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    out.chmod(0o600)
    print(f"wrote {out}: {found.count()} disagreement(s)")
    for section, texts in found.items.items():
        print(f"  {section}: {len(texts)}")


if __name__ == "__main__":
    main()
