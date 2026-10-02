"""Fill the QA Jira site from ``roster.py``: users, projects, epics, issues, sprints.

    uv run python -m scripts.qa_org.seed_jira --dry-run   # show the plan, write nothing
    uv run python -m scripts.qa_org.seed_jira             # apply it

Idempotent: everything is looked up before it is created, so re-running only
fills gaps. Inviting a user sends a real invite email to the plus-address.
Account ids, issue keys, board and sprint ids are recorded in a state file
outside the repo so the OpenProgram seeder can link identities without guessing.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import ISSUES, PEOPLE, PODS, PROJECTS, WORKSTREAMS, Issue, Person

STATE_FILE = Path("~/.config/oneai/openprogram-qa-state.json").expanduser()
TEMPLATE = "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic"
SPRINT_DAYS = 14
INVITE_ATTEMPTS = 4
MAX_INVITE_WAIT = 180


class Jira:
    def __init__(self, *, dry_run: bool) -> None:
        cfg = env.require(
            "OPENPROGRAM_JIRA_BASE_URL", "OPENPROGRAM_JIRA_EMAIL", "OPENPROGRAM_JIRA_API_TOKEN"
        )
        self.dry_run = dry_run
        self.client = httpx.Client(
            base_url=cfg["OPENPROGRAM_JIRA_BASE_URL"].rstrip("/"),
            auth=(cfg["OPENPROGRAM_JIRA_EMAIL"], cfg["OPENPROGRAM_JIRA_API_TOKEN"]),
            headers={"Accept": "application/json"},
            timeout=30,
        )

    def _get(self, path: str, params: dict[str, str]) -> object:
        response = self.client.get(path, params=params)
        if response.status_code == 404:
            return None
        _raise_for(response)
        return response.json() if response.content else None

    def get(self, path: str, **params: str) -> dict[str, Any]:
        """GET a JSON object; ``{}`` when it does not exist."""
        found = self._get(path, params)
        return found if isinstance(found, dict) else {}

    def get_list(self, path: str, **params: str) -> list[Any]:
        found = self._get(path, params)
        return found if isinstance(found, list) else []

    def write(
        self, method: str, path: str, body: dict[str, Any], what: str
    ) -> dict[str, Any] | None:
        """Send a write, or only describe it on a dry run (then returns ``None``)."""
        if self.dry_run:
            print(f"    would {what}")
            return None
        response = self.client.request(method, path, json=body)
        _raise_for(response)
        payload = response.json() if response.content else {}
        return payload if isinstance(payload, dict) else {}


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    raise SystemExit(
        f"{response.request.method} {response.request.url.path} -> "
        f"HTTP {response.status_code}: {response.text[:400]}"
    )


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        loaded: dict[str, Any] = json.loads(STATE_FILE.read_text())
        return loaded
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    STATE_FILE.chmod(0o600)


def ensure_users(jira: Jira, state: dict[str, Any]) -> dict[str, str]:
    print("== users")
    accounts: dict[str, str] = state.setdefault("jira_accounts", {})
    myself = jira.get("/rest/api/3/myself")
    owner = next(p for p in PEOPLE if "admin" in p.roles)
    accounts[owner.tag] = myself["accountId"]
    print(f"  {owner.name:<16} {myself['accountId']}  (site owner)")
    rate_limited = False
    for person in PEOPLE:
        if person is owner or person.jira_email is None:
            continue
        if person.tag in accounts:
            print(f"  {person.name:<16} {accounts[person.tag]}")
            continue
        found = jira.get_list("/rest/api/3/user/search", query=person.jira_email)
        if found:
            accounts[person.tag] = found[0]["accountId"]
            print(f"  {person.name:<16} {accounts[person.tag]}  (already on the site)")
            continue
        if rate_limited:
            print(f"  {person.name:<16} not invited yet (throttled); re-run later")
            continue
        account_id, rate_limited = invite(jira, person)
        if account_id is not None:
            accounts[person.tag] = account_id
            print(f"  {person.name:<16} {account_id}  (invited)")
    return accounts


def invite(jira: Jira, person: Person) -> tuple[str | None, bool]:
    """Invite one user; returns ``(account_id, gave_up)``.

    Atlassian throttles invitations on a new site far below the general REST
    limit: after a handful it answers 429 with a ``Retry-After`` of about a
    minute, and sometimes hangs or 504s instead. Wait out a short Retry-After;
    after a hang or 5xx, search first, because the invite may have landed.
    """
    if jira.dry_run:
        print(f"    would invite {person.name} <{person.jira_email}>")
        return None, False
    body = {"emailAddress": person.jira_email, "products": ["jira-software"]}
    for _attempt in range(INVITE_ATTEMPTS):
        try:
            response = jira.client.post("/rest/api/3/user", json=body, timeout=90)
        except httpx.TimeoutException:
            response = None
        if response is not None and response.is_success:
            return str(response.json()["accountId"]), False
        if response is not None and response.status_code != 429 and response.status_code < 500:
            _raise_for(response)
        found = jira.get_list("/rest/api/3/user/search", query=person.jira_email or "")
        if found:
            return str(found[0]["accountId"]), False
        status = "timed out" if response is None else f"HTTP {response.status_code}"
        wait = _retry_after(response)
        print(f"  {person.name:<16} invite {status}; waiting {wait}s")
        time.sleep(wait)
    return None, True


def _retry_after(response: httpx.Response | None) -> int:
    header = response.headers.get("retry-after") if response is not None else None
    if header and header.isdigit():
        return min(int(header) + 2, MAX_INVITE_WAIT)
    return 30


def project_members(project_id: str) -> set[str]:
    pod_ids = {pod.id for pod in PODS if project_id in pod.project_ids}
    return {p.tag for p in PEOPLE if any(m.pod_id in pod_ids for m in p.pods)}


def ensure_projects(jira: Jira, accounts: dict[str, str]) -> None:
    print("== projects")
    owner = next(p for p in PEOPLE if "admin" in p.roles)
    for project in PROJECTS:
        if jira.get(f"/rest/api/3/project/{project.jira_key}"):
            print(f"  {project.jira_key} exists")
        else:
            jira.write(
                "POST",
                "/rest/api/3/project",
                {
                    "key": project.jira_key,
                    "name": project.name,
                    "projectTypeKey": "software",
                    "projectTemplateKey": TEMPLATE,
                    "leadAccountId": accounts[owner.tag],
                    "assigneeType": "UNASSIGNED",
                },
                f"create project {project.jira_key} {project.name}",
            )
        if jira.dry_run and not jira.get(f"/rest/api/3/project/{project.jira_key}"):
            continue
        add_role_members(jira, project.jira_key, project_members(project.id), accounts)

    wanted = {issue.component for issue in ISSUES if issue.component}
    for key in sorted({issue.project for issue in ISSUES if issue.component}):
        components = jira.get_list(f"/rest/api/3/project/{key}/components")
        existing = {c["name"] for c in components}
        for name in sorted(wanted - existing):
            jira.write(
                "POST",
                "/rest/api/3/component",
                {"name": name, "project": key},
                f"create component {key}/{name}",
            )


def add_role_members(jira: Jira, key: str, tags: set[str], accounts: dict[str, str]) -> None:
    """Give the project's people the Member role, as a project admin would."""
    roles: dict[str, str] = jira.get(f"/rest/api/3/project/{key}/role")
    url = roles.get("Member") or roles.get("Developers")
    if url is None:
        print(f"  {key}: no Member/Developers role ({', '.join(roles)}); skipped role grants")
        return
    role_path = "/rest/api/3/project/" + url.split("/rest/api/3/project/", 1)[1]
    current = jira.get(role_path)
    have = {a.get("actorUser", {}).get("accountId") for a in current.get("actors", [])}
    ids = sorted(accounts[t] for t in tags if t in accounts and accounts[t] not in have)
    if ids:
        jira.write("POST", role_path, {"user": ids}, f"add {len(ids)} people to {key} Member role")


def existing_issues(jira: Jira, key: str) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    if jira.dry_run and not jira.get(f"/rest/api/3/project/{key}"):
        return found
    body = {"jql": f"project = {key}", "fields": ["summary", "issuetype"], "maxResults": 100}
    response = jira.client.post("/rest/api/3/search/jql", json=body)
    _raise_for(response)
    for item in response.json().get("issues", []):
        fields = item["fields"]
        found[(fields["issuetype"]["name"], fields["summary"])] = item["key"]
    return found


def ensure_issues(jira: Jira, accounts: dict[str, str], state: dict[str, Any]) -> None:
    print("== epics and issues")
    keys: dict[str, str] = state.setdefault("jira_issues", {})
    unassignable: list[str] = []
    for project in PROJECTS:
        have = existing_issues(jira, project.jira_key)
        epics = ensure_epics(jira, project.id, project.jira_key, have)
        keys.update({f"epic:{name}": key for name, key in epics.items()})
        for issue in (i for i in ISSUES if i.project == project.jira_key):
            key = have.get((issue.type, issue.summary)) or create_issue(
                jira, project.jira_key, issue_fields(issue, epics)
            )
            if key is None:
                continue
            keys[issue.summary] = key
            if issue.assignee and not assign(jira, key, accounts.get(issue.assignee)):
                unassignable.append(f"{key} -> {issue.assignee}")
            if issue.status != "To Do":
                transition(jira, key, issue.status)
    if unassignable:
        print("  could not assign (invite probably not accepted yet): " + ", ".join(unassignable))


def ensure_epics(
    jira: Jira, project_id: str, project_key: str, have: dict[tuple[str, str], str]
) -> dict[str, str]:
    epics: dict[str, str] = {}
    for ws in (w for w in WORKSTREAMS if w.project_id == project_id):
        key = have.get(("Epic", ws.epic)) or create_issue(
            jira, project_key, {"issuetype": {"name": "Epic"}, "summary": ws.epic}
        )
        if key:
            epics[ws.epic] = key
    return epics


def issue_fields(issue: Issue, epics: dict[str, str]) -> dict[str, Any]:
    fields: dict[str, Any] = {"issuetype": {"name": issue.type}, "summary": issue.summary}
    if issue.component:
        fields["components"] = [{"name": issue.component}]
    if issue.labels:
        fields["labels"] = list(issue.labels)
    if issue.priority:
        fields["priority"] = {"name": issue.priority}
    if issue.epic and issue.epic in epics:
        fields["parent"] = {"key": epics[issue.epic]}
    return fields


def create_issue(jira: Jira, project_key: str, fields: dict[str, Any]) -> str | None:
    body = {"fields": {"project": {"key": project_key}, **fields}}
    what = f"create {fields['issuetype']['name']} {project_key}: {fields['summary']}"
    created = jira.write("POST", "/rest/api/3/issue", body, what)
    if created is None:
        return None
    print(f"  {created['key']:<7} {fields['issuetype']['name']:<5} {fields['summary']}")
    return str(created["key"])


def assign(jira: Jira, key: str, account_id: str | None) -> bool:
    if account_id is None:
        return jira.dry_run
    if jira.dry_run:
        print(f"    would assign {key} to {account_id}")
        return True
    current = jira.get(f"/rest/api/3/issue/{key}", fields="assignee")
    assignee = (current.get("fields") or {}).get("assignee") or {}
    if assignee.get("accountId") == account_id:
        return True
    response = jira.client.put(f"/rest/api/3/issue/{key}/assignee", json={"accountId": account_id})
    return response.is_success


def transition(jira: Jira, key: str, status: str) -> None:
    if jira.dry_run:
        print(f"    would move {key} to {status}")
        return
    issue = jira.get(f"/rest/api/3/issue/{key}", fields="status")
    if issue.get("fields", {}).get("status", {}).get("name") == status:
        return
    options = jira.get(f"/rest/api/3/issue/{key}/transitions").get("transitions", [])
    match = next((t for t in options if t["to"]["name"].lower() == status.lower()), None)
    if match is None:
        names = ", ".join(t["to"]["name"] for t in options)
        print(f"  {key}: no transition to {status} (available: {names})")
        return
    body = {"transition": {"id": match["id"]}}
    jira.write("POST", f"/rest/api/3/issue/{key}/transitions", body, f"move {key} to {status}")


def ensure_sprints(jira: Jira, state: dict[str, Any]) -> None:
    print("== boards and sprints")
    keys: dict[str, str] = state.get("jira_issues", {})
    boards: dict[str, int] = state.setdefault("jira_boards", {})
    sprints: dict[str, int] = state.setdefault("jira_sprints", {})
    start = datetime.now(tz=UTC).replace(microsecond=0)
    end = start + timedelta(days=SPRINT_DAYS)
    for project in PROJECTS:
        if jira.dry_run and not jira.get(f"/rest/api/3/project/{project.jira_key}"):
            print(f"    would create {project.jira_key} Sprint 1 once the project's board exists")
            continue
        found = jira.get("/rest/agile/1.0/board", projectKeyOrId=project.jira_key)
        values = found.get("values", [])
        if not values:
            print(f"  {project.jira_key}: no board yet" + (" (dry run)" if jira.dry_run else ""))
            continue
        board_id = int(values[0]["id"])
        boards[project.jira_key] = board_id
        name = f"{project.jira_key} Sprint 1"
        existing = jira.get(f"/rest/agile/1.0/board/{board_id}/sprint")
        sprint = next((s for s in existing.get("values", []) if s["name"] == name), None)
        if sprint is None:
            sprint = jira.write(
                "POST",
                "/rest/agile/1.0/sprint",
                {
                    "name": name,
                    "originBoardId": board_id,
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                },
                f"create {name}",
            )
        if sprint is None:
            continue
        sprints[project.jira_key] = int(sprint["id"])
        members = [
            keys[i.summary]
            for i in ISSUES
            if i.project == project.jira_key and i.in_sprint and i.summary in keys
        ]
        if members:
            jira.write(
                "POST",
                f"/rest/agile/1.0/sprint/{sprint['id']}/issue",
                {"issues": members},
                f"put {len(members)} issues in {name}",
            )
        if sprint.get("state") != "active":
            jira.write(
                "POST",
                f"/rest/agile/1.0/sprint/{sprint['id']}",
                {"state": "active", "startDate": start.isoformat(), "endDate": end.isoformat()},
                f"start {name}",
            )
        print(f"  {name}: board {board_id}, sprint {sprint['id']}, {len(members)} issues")


def summary(accounts: dict[str, str]) -> None:
    print("== roster")
    for person in PEOPLE:
        jira = accounts.get(person.tag, "-") if person.in_jira else "not in Jira"
        print(f"  {person.name:<16} {_pods(person):<44} {jira}")


def _pods(person: Person) -> str:
    names = {pod.id: pod.name.removesuffix(" Pod") for pod in PODS}
    return ", ".join(names[m.pod_id] for m in person.pods) or "-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = parser.parse_args()
    jira = Jira(dry_run=args.dry_run)
    state = load_state()
    try:
        accounts = ensure_users(jira, state)
        ensure_projects(jira, accounts)
        ensure_issues(jira, accounts, state)
        ensure_sprints(jira, state)
        summary(accounts)
    finally:
        # Keep whatever was created even when a later step fails, so a re-run
        # resumes from it instead of re-discovering everything.
        if not args.dry_run:
            save_state(state)
            print(f"state written to {STATE_FILE}")


if __name__ == "__main__":
    main()
