"""Load the QA org into OpenProgram through its runtime config API.

    uv run python -m scripts.qa_org.seed_openprogram            # against http://127.0.0.1:8000

Everything goes through the same ``/config/*`` endpoints the admin console uses
(no direct database writes), so this also exercises them. Run it after
``use_real`` has pointed the stack at the real Slack and Jira, and after
``seed_jira`` has recorded account ids and board ids in the state file.

Order matters and mirrors what an admin would do: sync the Slack directory,
build program -> projects -> workstreams -> pods, add members from the
directory (their id IS their Slack id), put them in pods with a role, link
identities, then give each pod its scrum master and manager as escalation
contacts. Idempotent: existing nodes are updated, existing links are kept.

Workstreams are optional; pods are the grouping OpenProgram needs. The QA
org creates its workstreams but puts no task in them, so the persona views
leave them out (only Admin lists them) until work is linked to one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from scripts.qa_org.roster import (
    PEOPLE,
    PODS,
    PROGRAM_ID,
    PROGRAM_NAME,
    PROJECTS,
    WORKSTREAMS,
    Person,
)

STATE_FILE = Path("~/.config/oneai/openprogram-qa-state.json").expanduser()
CHECKIN_TIME = "09:30"
WEEKDAYS = [0, 1, 2, 3, 4]


class Api:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=60)

    def call(self, method: str, path: str, body: object = None) -> httpx.Response:
        return self.client.request(method, path, json=body)

    def ok(self, method: str, path: str, body: object = None) -> Any:  # noqa: ANN401
        response = self.call(method, path, body)
        if not response.is_success:
            raise SystemExit(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:400]}"
            )
        return response.json() if response.content else None

    def upsert(self, collection: str, node_id: str, fields: dict[str, Any]) -> str:
        """Create a config node, or update it in place when it already exists."""
        response = self.call("POST", f"/config/{collection}", {"id": node_id, **fields})
        if response.status_code == 409:
            self.ok("PUT", f"/config/{collection}/{node_id}", fields)
            return "updated"
        if not response.is_success:
            raise SystemExit(
                f"POST /config/{collection} {node_id} -> "
                f"HTTP {response.status_code}: {response.text[:400]}"
            )
        return "created"

    def link(self, path: str, body: object = None) -> None:
        response = self.call("POST", path, body)
        if response.status_code == 409 or response.is_success:
            return
        raise SystemExit(f"POST {path} -> HTTP {response.status_code}: {response.text[:400]}")


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} missing: run `python -m scripts.qa_org.seed_jira` first")
    loaded: dict[str, Any] = json.loads(STATE_FILE.read_text())
    return loaded


def sync_directory(api: Api) -> dict[str, dict[str, Any]]:
    print("== Slack directory")
    synced = api.ok("POST", "/config/directory/sync")
    print(f"  synced {synced['synced_count']} users, deactivated {synced['deactivated_count']}")
    found = api.ok("GET", "/config/directory/users?limit=100")
    by_email = {str(u.get("email") or "").lower(): u for u in found["items"]}
    missing = [p.name for p in PEOPLE if p.slack_email.lower() not in by_email]
    if missing:
        raise SystemExit(f"not in the synced directory (have they joined Slack?): {missing}")
    return by_email


def build_hierarchy(api: Api, state: dict[str, Any]) -> None:
    print("== program, projects, workstreams, pods")
    boards: dict[str, int] = state.get("jira_boards", {})
    print(f"  {PROGRAM_ID}: {api.upsert('programs', PROGRAM_ID, {'name': PROGRAM_NAME})}")
    for project in PROJECTS:
        board = boards.get(project.jira_key)
        result = api.upsert(
            "projects",
            project.id,
            {
                "name": project.name,
                "code": project.jira_key,
                "jira_project_key": project.jira_key,
                "jira_board_id": str(board) if board else None,
                "github_repos": list(project.repos),
            },
        )
        api.link(f"/config/projects/{project.id}/program", {"program_id": PROGRAM_ID})
        print(f"  {project.id}: {result} (Jira {project.jira_key}, board {board})")
    for ws in WORKSTREAMS:
        result = api.upsert("workstreams", ws.id, {"name": ws.name, "github_repos": list(ws.repos)})
        api.link(f"/config/projects/{ws.project_id}/workstreams/{ws.id}")
        print(f"  {ws.id}: {result}")
    for pod in PODS:
        fields = {
            "name": pod.name,
            "jira_filter_jql": pod.jira_filter_jql,
            "github_repos": list(pod.repos),
        }
        result = api.upsert("pods", pod.id, fields)
        for project_id in pod.project_ids:
            api.link(f"/config/pods/{pod.id}/projects/{project_id}")
        for ws_id in pod.workstream_ids:
            api.link(f"/config/pods/{pod.id}/workstreams/{ws_id}")
        print(f"  {pod.id}: {result} ({pod.jira_filter_jql or 'whole project'})")


def add_members(
    api: Api,
    directory: dict[str, dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, str]:
    """Add, place and link every person; returns roster tag -> member (Slack) id."""
    print("== members")
    accounts: dict[str, str] = state.get("jira_accounts", {})
    member_ids: dict[str, str] = {}
    for person in PEOPLE:
        slack_id = str(directory[person.slack_email.lower()]["external_id"])
        response = api.call("POST", "/config/members/from-directory", {"external_ids": [slack_id]})
        if response.status_code not in (200, 201, 409):
            raise SystemExit(f"add {person.name} -> HTTP {response.status_code}: {response.text}")
        api.ok(
            "PUT",
            f"/config/members/{slack_id}",
            {
                "metadata": {
                    "title": person.title,
                    "app_roles": ",".join(person.roles),
                    "timezone": person.timezone,
                }
            },
        )
        for membership in person.pods:
            api.link(
                f"/config/pods/{membership.pod_id}/members/{slack_id}", {"role": membership.role}
            )
        api.ok(
            "PUT",
            f"/config/members/{slack_id}/checkin-preference",
            {"local_time": CHECKIN_TIME, "timezone": person.timezone, "weekdays": WEEKDAYS},
        )
        member_ids[person.tag] = slack_id
        jira_id = accounts.get(person.tag) if person.in_jira else None
        link: dict[str, str | None] = {"chat_user_id": slack_id}
        if jira_id:
            link["jira_account_id"] = jira_id
        if person.gitlab_username:
            link["vcs_username"] = person.gitlab_username
        api.ok("PUT", f"/config/members/{slack_id}/identity-link", link)
        jira = jira_id or "-"
        git = person.gitlab_username or "-"
        print(f"  {person.name:<16} {slack_id}  Jira {jira:<46} Git {git:<11} {_pods(person)}")
    return member_ids


def set_escalation_contacts(api: Api, member_ids: dict[str, str]) -> None:
    """Each pod's scrum master, then its manager, as the roster names them."""
    print("== escalation contacts")
    for pod in PODS:
        contacts: dict[str, dict[str, str]] = {}
        for rung, role in (("scrum_master", "scrum_master"), ("manager", "manager")):
            holder = next(
                (p for p in PEOPLE if any(m.pod_id == pod.id and m.role == role for m in p.pods)),
                None,
            )
            if holder is not None:
                contacts[rung] = {"member_id": member_ids[holder.tag]}
        if contacts:
            api.ok("PUT", f"/config/pods/{pod.id}/escalation-contacts", contacts)
        name_by_id = {member_ids[p.tag]: p.name for p in PEOPLE}
        shown = ", ".join(f"{rung} {name_by_id[c['member_id']]}" for rung, c in contacts.items())
        print(f"  {pod.id}: {shown or 'none'}")


def _pods(person: Person) -> str:
    names = {pod.id: pod.name.removesuffix(" Pod") for pod in PODS}
    return ", ".join(f"{names[m.pod_id]}({m.role})" for m in person.pods) or "no pod"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="OpenProgram backend")
    args = parser.parse_args()
    api = Api(args.api)
    status = api.ok("GET", "/api/v1/auth/status")
    print(f"backend: demo_mode={status.get('demo_mode')} chat_enabled={status.get('chat_enabled')}")
    state = load_state()
    directory = sync_directory(api)
    build_hierarchy(api, state)
    member_ids = add_members(api, directory, state)
    set_escalation_contacts(api, member_ids)
    unmapped = api.ok("GET", "/config/members/unmapped")
    print(f"== unmapped members: {[m.get('name') for m in unmapped] or 'none'}")


if __name__ == "__main__":
    main()
