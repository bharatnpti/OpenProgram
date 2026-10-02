"""Write a private reference of the live QA org: URLs, people, hierarchy, Jira, GitLab.

    uv run python -m scripts.qa_org.write_setup_doc            # -> ../openprogram-qa/QA-ORG.md
    uv run python -m scripts.qa_org.write_setup_doc --out FILE

Everything is read live, and read-only, from Slack, Jira, GitLab, the
OpenProgram API and the seeders' state file, so re-running it after a test shows
the current statuses, assignees and merge requests. The output names real
addresses, workspace URLs and account ids. It is written outside the repository
on purpose, because the repository is public. It never contains a password or
token, only the names of the secrets-file keys that hold them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import (
    GIT_WORK,
    GITLAB_GROUP,
    ISSUES,
    PEOPLE,
    PODS,
    PROGRAM_NAME,
    PROJECTS,
    WORKSTREAMS,
    Person,
    Pod,
    git_branch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT.parent / "openprogram-qa" / "QA-ORG.md"
STATE_FILE = Path("~/.config/oneai/openprogram-qa-state.json").expanduser()
API = "http://127.0.0.1:8000"
CONSOLE = "http://[::1]:5174"
GITLAB_WEB = "http://localhost:8929"
ROLE_ORDER = ("manager", "product_owner", "scrum_master", "developer")
ROLE_LABEL = {
    "manager": "manager",
    "product_owner": "PO",
    "scrum_master": "SM",
    "developer": "dev",
}

SECRET_KEYS = (
    ("OPENPROGRAM_QA_MAIL_BASE", "the mailbox every plus-address delivers to"),
    ("OPENPROGRAM_SLACK_BOT_TOKEN", "Slack bot token (xoxb-)"),
    ("OPENPROGRAM_SLACK_SIGNING_SECRET", "verifies inbound Slack webhooks"),
    ("OPENPROGRAM_JIRA_BASE_URL / _EMAIL / _API_TOKEN", "Jira site and Asha's API token"),
    ("OPENPROGRAM_QA_GITLAB_ROOT_PASSWORD", "GitLab UI sign-in as root"),
    ("OPENPROGRAM_QA_GITLAB_ADMIN_TOKEN", "root api+sudo, used only by seed_gitlab"),
    ("OPENPROGRAM_GITLAB_TOKEN", "openprogram-bot read_api, what OpenProgram syncs with"),
)


def _get(client: httpx.Client, path: str, **params: str) -> Any:  # noqa: ANN401
    try:
        response = client.get(path, params=params)
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


def slack_facts() -> dict[str, Any]:
    token = env.get("OPENPROGRAM_SLACK_BOT_TOKEN")
    if token is None:
        return {}
    with httpx.Client(
        base_url="https://slack.com/api",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    ) as client:
        auth = client.post("/auth.test").json()
        ids: dict[str, str] = {}
        cursor = ""
        while True:
            page = client.get("/users.list", params={"limit": "200", "cursor": cursor}).json()
            for member in page.get("members", []):
                email = str(member.get("profile", {}).get("email") or "").lower()
                if email:
                    ids[email] = str(member["id"])
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
    return {"team": auth.get("team"), "url": auth.get("url"), "ids": ids}


def jira_facts(state: dict[str, Any]) -> dict[str, Any]:
    base = env.get("OPENPROGRAM_JIRA_BASE_URL")
    email = env.get("OPENPROGRAM_JIRA_EMAIL")
    token = env.get("OPENPROGRAM_JIRA_API_TOKEN")
    if not (base and email and token):
        return {}
    keys = ", ".join(p.jira_key for p in PROJECTS)
    with httpx.Client(base_url=base.rstrip("/"), auth=(email, token), timeout=30) as client:
        found = client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": f"project in ({keys}) ORDER BY key ASC",
                "fields": ["summary", "status", "assignee", "issuetype", "components", "labels"],
                "maxResults": 200,
            },
        ).json()
        sprints: dict[str, dict[str, Any]] = {}
        for project_key, sprint_id in state.get("jira_sprints", {}).items():
            sprint = _get(client, f"/rest/agile/1.0/sprint/{sprint_id}") or {}
            members = _get(
                client, f"/rest/agile/1.0/sprint/{sprint_id}/issue", fields="key", maxResults="200"
            )
            sprint["issue_keys"] = {i["key"] for i in (members or {}).get("issues", [])}
            sprints[project_key] = sprint
    return {"base": base.rstrip("/"), "issues": found.get("issues", []), "sprints": sprints}


def gitlab_facts() -> dict[str, Any]:
    token = env.get("OPENPROGRAM_GITLAB_TOKEN")
    if token is None:
        return {}
    with httpx.Client(
        base_url=f"{GITLAB_WEB}/api/v4", headers={"PRIVATE-TOKEN": token}, timeout=30
    ) as client:
        projects = _get(client, f"/groups/{GITLAB_GROUP}/projects", per_page="100") or []
        mrs = _get(client, f"/groups/{GITLAB_GROUP}/merge_requests", state="all", per_page="100")
    paths = {p["id"]: p["path_with_namespace"] for p in projects}
    return {"projects": projects, "mrs": mrs or [], "paths": paths}


def openprogram_facts() -> dict[str, Any]:
    with httpx.Client(base_url=API, timeout=30) as client:
        status = _get(client, "/api/v1/auth/status")
        if status is None:
            return {}
        members = _get(client, "/config/members") or []
        links = {m["id"]: _get(client, f"/config/members/{m['id']}/identity-link") for m in members}
        unmapped = _get(client, "/config/members/unmapped") or []
        personas = (_get(client, "/api/v1/auth/dev-users") or {}).get("items", [])
    return {"status": status, "links": links, "unmapped": unmapped, "personas": personas}


def schedules() -> list[tuple[str, str, str]]:
    query = (
        "select schedule_name, schedule, status from dbos.workflow_schedules "
        "where schedule_name like 'openprogram-%' order by 1;"
    )
    command = ["docker", "exec", "openprogram-postgres-1", "psql", "-U", "openprogram"]
    result = subprocess.run(
        [*command, "-d", "openprogram", "-At", "-F", "|", "-c", query],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    rows = [line.split("|") for line in result.stdout.splitlines() if line.count("|") == 2]
    return [(row[0], row[1], row[2]) for row in rows]


def tunnel_hostname() -> str | None:
    for port in range(20241, 20246):
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/quicktunnel", timeout=2)
        except httpx.HTTPError:
            continue
        hostname = response.json().get("hostname") if response.status_code == 200 else None
        if hostname:
            return str(hostname)
    return None


def table(header: Iterable[str], rows: Iterable[Iterable[object]]) -> list[str]:
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in rows:
        cells = [str(cell).replace("|", "\\|") if cell not in (None, "") else "—" for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines + [""]


def pod_people(pod: Pod) -> dict[str, list[str]]:
    people: dict[str, list[str]] = {role: [] for role in ROLE_ORDER}
    for person in PEOPLE:
        for membership in person.pods:
            if membership.pod_id == pod.id:
                people[membership.role].append(person.name.split()[0])
    return people


def render(state: dict[str, Any]) -> str:  # noqa: C901 - one linear document
    slack = slack_facts()
    jira = jira_facts(state)
    gitlab = gitlab_facts()
    op = openprogram_facts()
    tunnel = tunnel_hostname()
    accounts: dict[str, str] = state.get("jira_accounts", {})
    slack_ids: dict[str, str] = slack.get("ids", {})
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = [
        "# OpenProgram QA org: live setup",
        "",
        f"Generated {now} from Slack, Jira, GitLab and the OpenProgram API. Regenerate with",
        "`uv run python -m scripts.qa_org.write_setup_doc` from the repo.",
        "",
        "**Private.** This names real addresses, workspace URLs and account ids. Keep it",
        "out of the (public) repository. It contains no passwords or tokens.",
        "",
        "## URLs",
        "",
    ]
    jira_base = jira.get("base", "")
    tunnel_url = f"https://{tunnel}" if tunnel else None
    out += table(
        ("What", "Where", "How you get in"),
        [
            ("OpenProgram console", CONSOLE, "`npm run dev --prefix frontend-v2`; act as anyone"),
            ("OpenProgram API", f"{API} ([docs]({API}/docs))", "dev auth, no login"),
            ("Slack workspace", slack.get("url"), "each person's own email + password"),
            ("Slack app settings", "https://api.slack.com/apps", "as Asha"),
            ("Jira site", jira_base, "as Asha (site admin)"),
            *(
                (
                    f"Jira board {p.jira_key}",
                    f"{jira_base}/jira/software/c/projects/{p.jira_key}/boards/"
                    f"{state.get('jira_boards', {}).get(p.jira_key, '?')}",
                    "",
                )
                for p in PROJECTS
            ),
            (
                "GitLab",
                f"{GITLAB_WEB}/{GITLAB_GROUP}",
                "`root` + OPENPROGRAM_QA_GITLAB_ROOT_PASSWORD",
            ),
            ("Webhook gate (local)", "http://127.0.0.1:8787", "forwards POST /webhooks/chat/*"),
            ("Public tunnel", tunnel_url or "not running", "changes on every restart"),
            (
                "Slack Event Subscriptions URL",
                f"{tunnel_url}/webhooks/chat/slack" if tunnel_url else "start the tunnel first",
                "bot event `message.im`",
            ),
        ],
    )
    out += [
        "## Credentials",
        "",
        "All in `~/.config/oneai/secrets.env`. Never copy values out.",
        "",
    ]
    out += table(("Key", "What it is"), SECRET_KEYS)

    out += ["## People", ""]
    rows = []
    for person in PEOPLE:
        pods = ", ".join(f"{_pod_name(m.pod_id)} ({ROLE_LABEL[m.role]})" for m in person.pods)
        rows.append(
            (
                person.name,
                person.title,
                ", ".join(person.roles),
                pods or "no pod",
                person.timezone,
                person.slack_email,
                slack_ids.get(person.slack_email.lower(), "not joined"),
                _jira_cell(person, accounts),
                person.gitlab_username,
            )
        )
    out += table(
        ("Name", "Title", "App roles", "Pods", "TZ", "Slack email", "Slack id", "Jira", "GitLab"),
        rows,
    )
    out += ["Why each person is there:", ""]
    out += [f"- **{p.name}**: {p.scenario}." for p in PEOPLE if p.scenario] + [""]

    out += ["## Hierarchy", "", "```text", PROGRAM_NAME]
    for index, project in enumerate(PROJECTS):
        last_project = index == len(PROJECTS) - 1
        branch, pad = ("└─ ", "   ") if last_project else ("├─ ", "│  ")
        repos = ", ".join(r.split("/", 1)[1] for r in project.repos)
        out.append(f"{branch}{project.name} [{project.jira_key}]  repos: {repos}")
        children: list[str] = [
            f"workstream {w.name}  ({', '.join(r.split('/', 1)[1] for r in w.repos)})"
            for w in WORKSTREAMS
            if w.project_id == project.id
        ]
        for pod in (p for p in PODS if project.id in p.project_ids):
            people = pod_people(pod)
            shared = " (shared)" if len(pod.project_ids) > 1 else ""
            who = "; ".join(
                f"{ROLE_LABEL[role]}: {', '.join(names)}" for role, names in people.items() if names
            )
            if not people["manager"]:
                who += "; no manager"
            scope = pod.jira_filter_jql or "whole project"
            children.append(f"pod {pod.name}{shared}  [{scope}]  {who}")
        for child_index, child in enumerate(children):
            twig = "└─ " if child_index == len(children) - 1 else "├─ "
            out.append(f"{pad}{twig}{child}")
    no_pod = [p.name for p in PEOPLE if not p.pods]
    out += [f"(no pod: {', '.join(no_pod)})", "```", ""]

    out += ["## Pods", ""]
    out += table(
        ("Pod", "Projects", "Jira scope", "Repos", "Manager", "PO", "SM", "Developers"),
        (
            (
                pod.name,
                ", ".join(_project_key(pid) for pid in pod.project_ids),
                f"`{pod.jira_filter_jql}`" if pod.jira_filter_jql else "whole project",
                ", ".join(r.split("/", 1)[1] for r in pod.repos),
                *(", ".join(pod_people(pod)[role]) for role in ROLE_ORDER),
            )
            for pod in PODS
        ),
    )

    out += ["## Jira", ""]
    sprints: dict[str, dict[str, Any]] = jira.get("sprints", {})
    out += table(
        ("Project", "Key", "Board", "Sprint", "State", "Ends"),
        (
            (
                p.name,
                p.jira_key,
                state.get("jira_boards", {}).get(p.jira_key),
                sprints.get(p.jira_key, {}).get("name"),
                sprints.get(p.jira_key, {}).get("state"),
                str(sprints.get(p.jira_key, {}).get("endDate", ""))[:10],
            )
            for p in PROJECTS
        ),
    )
    in_sprint = set().union(*(s.get("issue_keys", set()) for s in sprints.values()))
    names_by_account = {account: _person(tag).name for tag, account in accounts.items()}
    issue_rows = []
    for item in jira.get("issues", []):
        fields = item["fields"]
        assignee_ref = fields.get("assignee") or {}
        # Invitees who have not accepted yet show their address as a display name.
        account_id = str(assignee_ref.get("accountId") or "")
        assignee = names_by_account.get(account_id) or assignee_ref.get("displayName")
        issue_rows.append(
            (
                f"[{item['key']}]({jira_base}/browse/{item['key']})",
                fields["issuetype"]["name"],
                fields["summary"],
                assignee or "unassigned",
                fields["status"]["name"],
                ", ".join(c["name"] for c in fields.get("components") or []),
                ", ".join(fields.get("labels") or []),
                "yes" if item["key"] in in_sprint else "backlog",
            )
        )
    if issue_rows:
        out += table(
            ("Key", "Type", "Summary", "Assignee", "Status", "Component", "Labels", "Sprint"),
            issue_rows,
        )
    planned = {i.summary: i.assignee for i in ISSUES if i.assignee}
    waiting = sorted(
        {planned[s] for s in planned if planned[s] not in accounts and _person(planned[s]).in_jira}
    )
    if waiting:
        names = ", ".join(_person(t).name for t in waiting)
        out += [
            f"Not yet on the site (invite throttled): {names}. Their issues stay",
            "unassigned until `seed_jira` invites them; then re-run `seed_openprogram`.",
            "",
        ]

    out += ["## GitLab", ""]
    out += table(
        ("Repository", "Projects", "Pods"),
        (
            (
                f"[{repo}]({GITLAB_WEB}/{repo})",
                ", ".join(p.jira_key for p in PROJECTS if repo in p.repos),
                ", ".join(_pod_name(p.id) for p in PODS if repo in p.repos),
            )
            for repo in sorted({r for p in PROJECTS for r in p.repos})
        ),
    )
    mrs_by_branch = {(mr["project_id"], mr["source_branch"]): mr for mr in gitlab.get("mrs", [])}
    ids_by_path = {path: pid for pid, path in gitlab.get("paths", {}).items()}
    keys: dict[str, str] = state.get("jira_issues", {})
    work_rows = []
    for work in GIT_WORK:
        key = keys.get(work.issue, "?")
        repo = f"{GITLAB_GROUP}/{work.repo}"
        branch = git_branch(work, key)
        mr = mrs_by_branch.get((ids_by_path.get(repo), branch))
        mr_cell = (
            f"[!{mr['iid']} {'draft' if mr.get('draft') else mr['state']}]({mr['web_url']})"
            if mr
            else ("pushed to main" if work.to_main else "no MR")
        )
        work_rows.append((work.repo, branch, _person(work.author).name, key, mr_cell))
    out += table(("Repo", "Branch", "Author", "Jira", "Merge request"), work_rows)

    out += ["## OpenProgram", ""]
    status = op.get("status") or {}
    out += [
        f"- Tenant `qa`, acting by default as `{status.get('user', {}).get('subject', '?')}`",
        f"  (Asha); {len(op.get('personas', []))} people in the persona picker.",
        "- Providers: Slack (chat + directory), Jira, GitLab; the shared `.env` stays the",
        "  mock demo, the docker stack reads `.env.qa`.",
        "",
    ]
    link_rows = []
    for person in PEOPLE:
        slack_id = slack_ids.get(person.slack_email.lower())
        link = (op.get("links") or {}).get(slack_id) or {}
        link_rows.append(
            (
                person.name,
                link.get("chat_user_id"),
                link.get("jira_account_id") or "not linked",
                link.get("jira_email"),
                link.get("vcs_username"),
            )
        )
    out += ["Identity links:", ""]
    out += table(
        ("Member", "chat_user_id", "jira_account_id", "jira_email", "vcs_username"), link_rows
    )
    blocking = ("chat_user_id", "jira_account_id")
    unmapped = [
        f"{m['name']} (no {' / no '.join(f for f in m['missing'] if f in blocking)})"
        for m in op.get("unmapped", [])
    ]
    out += [f"Unmapped right now: {'; '.join(unmapped) or 'none'}.", ""]
    rows_s = schedules()
    if rows_s:
        out += ["Workflow schedules (cron in UTC):", ""]
        out += table(("Schedule", "Cron", "Status"), rows_s)
    out += [
        "## Known gaps going into testing",
        "",
        "- Check-in catch-up (`openprogram-checkin-reconcile`) is paused; the 09:30 UTC",
        "  weekday fan-out is active. `CROSS_PERSON_AUTO_NOTIFY` is pinned off.",
        '- Unknown git authors become people: GitLab root shows up as "Administrator".',
        "- Only default-branch commits sync; branch work shows only through its MR.",
        "- Slack replies need the Event Subscriptions URL above to be saved in the app.",
        "",
    ]
    return "\n".join(out)


def _pod_name(pod_id: str) -> str:
    return next(p.name for p in PODS if p.id == pod_id).removesuffix(" Pod")


def _project_key(project_id: str) -> str:
    return next(p.jira_key for p in PROJECTS if p.id == project_id)


def _person(tag: str) -> Person:
    return next(p for p in PEOPLE if p.tag == tag)


def _jira_cell(person: Person, accounts: dict[str, str]) -> str:
    if not person.in_jira:
        return "no seat"
    account = accounts.get(person.tag)
    if account is None:
        return f"not invited yet ({person.jira_email})"
    if person.jira_tag:
        return f"{account} via {person.jira_email}"
    return account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    out: Path = args.out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(state))
    out.chmod(0o600)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
