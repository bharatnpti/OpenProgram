"""Fill the QA GitLab from ``roster.py``: users, the group, repositories, branches, MRs.

    uv run python -m scripts.qa_org.seed_gitlab

Runs against the container from ``docker-compose.qa.yml`` (http://localhost:8929).
Idempotent: users, group, projects, branches and MRs are looked up before they
are created, so re-running only fills gaps.

Two tokens, both generated into the secrets file beforehand and set here with
``gitlab-rails runner`` (the API cannot create a token with a chosen value):

* ``OPENPROGRAM_QA_GITLAB_ADMIN_TOKEN`` (root; ``api``, ``sudo``) seeds. ``Sudo``
  makes every commit, branch and MR appear as its author, not as root.
* ``OPENPROGRAM_GITLAB_TOKEN`` (``openprogram-bot``, Reporter, ``read_api``) is
  what OpenProgram syncs with: least privilege, like a real integration.

GitLab sends no mail here (no SMTP), so plus-addresses are only profile data.
GitLab user, project and MR ids go to the shared QA state file.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import (
    GIT_WORK,
    GITLAB_GROUP,
    ISSUES,
    PEOPLE,
    PROJECTS,
    GitWork,
    git_branch,
)

API = "http://localhost:8929/api/v4"
CONTAINER = "openprogram-gitlab-1"
STATE_FILE = Path("~/.config/oneai/openprogram-qa-state.json").expanduser()
BOT = "openprogram-bot"
DEVELOPER, MAINTAINER, REPORTER = 30, 40, 20

_RUNNER = """
user = User.find_by_username!(ENV.fetch('TOKEN_USER'))
user.personal_access_tokens.active.where(name: ENV.fetch('TOKEN_NAME')).each(&:revoke!)
token = user.personal_access_tokens.build(
  name: ENV.fetch('TOKEN_NAME'),
  scopes: ENV.fetch('TOKEN_SCOPES').split(','),
  expires_at: 364.days.from_now.to_date,
)
if token.respond_to?(:organization=)
  token.organization = user.organizations.first || Organizations::Organization.first
end
token.set_token(ENV.fetch('TOKEN_VALUE'))
token.save!
puts "token #{token.name} set for #{user.username}"
"""


class GitLab:
    def __init__(self, token: str) -> None:
        self.client = httpx.Client(base_url=API, headers={"PRIVATE-TOKEN": token}, timeout=60)

    def call(
        self,
        method: str,
        path: str,
        *,
        sudo: str | None = None,
        params: dict[str, str] | None = None,
        json: object = None,
    ) -> httpx.Response:
        headers = {"Sudo": sudo} if sudo else {}
        return self.client.request(method, path, headers=headers, params=params, json=json)

    def ok(
        self,
        method: str,
        path: str,
        *,
        sudo: str | None = None,
        params: dict[str, str] | None = None,
        json: object = None,
    ) -> Any:  # noqa: ANN401
        response = self.call(method, path, sudo=sudo, params=params, json=json)
        if not response.is_success:
            raise SystemExit(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else None

    def find(self, path: str) -> Any:  # noqa: ANN401
        response = self.call("GET", path)
        return response.json() if response.status_code == 200 else None


def token_works(token: str) -> bool:
    response = httpx.get(f"{API}/user", headers={"PRIVATE-TOKEN": token}, timeout=30)
    return response.status_code == 200


def set_token(user: str, name: str, scopes: str, value: str) -> None:
    """Install a token with a known value through Rails, the only way to choose it."""
    run_env = {
        **os.environ,
        "TOKEN_USER": user,
        "TOKEN_NAME": name,
        "TOKEN_SCOPES": scopes,
        "TOKEN_VALUE": value,
    }
    names = ("TOKEN_USER", "TOKEN_NAME", "TOKEN_SCOPES", "TOKEN_VALUE")
    command = ["docker", "exec", *[f"-e{key}" for key in names], CONTAINER]
    result = subprocess.run(
        [*command, "gitlab-rails", "runner", _RUNNER],
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"gitlab-rails runner failed: {result.stderr.strip()[-600:]}")
    print(f"  {result.stdout.strip().splitlines()[-1]}")


def ensure_user(gl: GitLab, username: str, name: str, email: str) -> int:
    found = gl.ok("GET", "/users", params={"username": username})
    if found:
        return int(found[0]["id"])
    created = gl.ok(
        "POST",
        "/users",
        json={
            "username": username,
            "name": name,
            "email": email,
            # Never used: nobody signs in as these people; tokens or Sudo act for them.
            "password": secrets.token_urlsafe(24),
            "skip_confirmation": True,
        },
    )
    return int(created["id"])


def ensure_users(gl: GitLab, state: dict[str, Any]) -> dict[str, int]:
    print("== users")
    ids: dict[str, int] = state.setdefault("gitlab_users", {})
    for person in PEOPLE:
        if person.gitlab_username is None:
            continue
        ids[person.gitlab_username] = ensure_user(
            gl, person.gitlab_username, person.name, person.slack_email
        )
        print(f"  {person.gitlab_username:<12} {person.name}")
    base = env.require("OPENPROGRAM_QA_MAIL_BASE")["OPENPROGRAM_QA_MAIL_BASE"]
    local, _, domain = base.partition("@")
    ids[BOT] = ensure_user(gl, BOT, "OpenProgram", f"{local}+openprogram-bot@{domain}")
    print(f"  {BOT:<12} integration account")
    return ids


def ensure_group(gl: GitLab, users: dict[str, int]) -> int:
    print("== group")
    group = gl.find(f"/groups/{GITLAB_GROUP}") or gl.ok(
        "POST",
        "/groups",
        json={"name": "Acme", "path": GITLAB_GROUP, "visibility": "private"},
    )
    group_id = int(group["id"])
    owner = next(p for p in PEOPLE if "admin" in p.roles)
    developers = {p.gitlab_username for p in PEOPLE if "dev" in p.roles}
    for username, user_id in users.items():
        # Exec, PO and SM accounts read; only developers push.
        level = DEVELOPER if username in developers else REPORTER
        if username == owner.gitlab_username:
            level = MAINTAINER
        response = gl.call(
            "POST",
            f"/groups/{group_id}/members",
            json={"user_id": user_id, "access_level": level},
        )
        if response.status_code not in (201, 409):
            raise SystemExit(f"add {username} to group -> {response.status_code} {response.text}")
    print(f"  {GITLAB_GROUP} (id {group_id}), {len(users)} members")
    return group_id


def ensure_projects(gl: GitLab, group_id: int, state: dict[str, Any]) -> dict[str, int]:
    print("== repositories")
    ids: dict[str, int] = state.setdefault("gitlab_projects", {})
    names = sorted({path.split("/", 1)[1] for project in PROJECTS for path in project.repos})
    for name in names:
        found = gl.find(f"/projects/{quote(f'{GITLAB_GROUP}/{name}', safe='')}")
        project = found or gl.ok(
            "POST",
            "/projects",
            json={
                "name": name,
                "path": name,
                "namespace_id": group_id,
                "initialize_with_readme": True,
                "default_branch": "main",
                "visibility": "private",
            },
        )
        ids[name] = int(project["id"])
        print(f"  {GITLAB_GROUP}/{name}")
    return ids


def push_work(
    gl: GitLab, projects: dict[str, int], keys: dict[str, str], state: dict[str, Any]
) -> None:
    print("== commits and merge requests")
    mrs: dict[str, int] = state.setdefault("gitlab_mrs", {})
    for work in GIT_WORK:
        key = keys.get(work.issue)
        if key is None:
            raise SystemExit(f"no Jira key for {work.issue!r}: run seed_jira first")
        author = _username(work.author)
        project_id = projects[work.repo]
        if work.to_main:
            _ensure_project_maintainer(gl, project_id, author)
        branch = git_branch(work, key)
        pushed = _push_commits(gl, project_id, branch, author, key, work)
        mr_iid = _ensure_mr(gl, project_id, branch, author, key, work) if work.mr else None
        if mr_iid is not None:
            mrs[f"{work.repo}!{branch}"] = mr_iid
        state_word = work.mr or ("pushed to main" if work.to_main else "branch only")
        print(f"  {work.repo:<18} {branch:<44} {author:<11} {pushed} new, {state_word}")


def _push_commits(
    gl: GitLab, project_id: int, branch: str, author: str, key: str, work: GitWork
) -> int:
    pushed = 0
    for index, message in enumerate(work.commits, start=1):
        path = f"changes/{key.lower()}-{index}.md"
        exists = gl.call(
            "GET",
            f"/projects/{project_id}/repository/files/{quote(path, safe='')}",
            params={"ref": branch},
        )
        if exists.status_code == 200:
            continue
        branch_exists = gl.call(
            "GET", f"/projects/{project_id}/repository/branches/{quote(branch, safe='')}"
        )
        body: dict[str, Any] = {
            "branch": branch,
            "commit_message": f"{key}: {message}",
            "actions": [
                {"action": "create", "file_path": path, "content": f"# {key}\n\n{message}\n"}
            ],
        }
        if branch_exists.status_code == 404:
            body["start_branch"] = "main"
        _commit(gl, project_id, author, body)
        pushed += 1
    return pushed


def _commit(gl: GitLab, project_id: int, author: str, body: dict[str, Any]) -> None:
    """Commit as ``author``; a 403 right after a role change is GitLab still
    refreshing project authorizations in the background, so wait it out."""
    path = f"/projects/{project_id}/repository/commits"
    for _ in range(12):
        response = gl.call("POST", path, sudo=author, json=body)
        if response.status_code != 403:
            break
        time.sleep(5)
    if not response.is_success:
        raise SystemExit(f"POST {path} -> HTTP {response.status_code}: {response.text[:300]}")


def _ensure_mr(
    gl: GitLab, project_id: int, branch: str, author: str, key: str, work: GitWork
) -> int:
    found = gl.ok(
        "GET",
        f"/projects/{project_id}/merge_requests",
        params={"source_branch": branch, "state": "all"},
    )
    if found:
        mr = found[0]
    else:
        title = f"{key} {work.issue}"
        mr = gl.ok(
            "POST",
            f"/projects/{project_id}/merge_requests",
            sudo=author,
            json={
                "source_branch": branch,
                "target_branch": "main",
                "title": f"Draft: {title}" if work.mr == "draft" else title,
                "remove_source_branch": False,
            },
        )
    if work.mr == "merged" and mr["state"] != "merged":
        _merge(gl, project_id, int(mr["iid"]))
    return int(mr["iid"])


def _merge(gl: GitLab, project_id: int, iid: int) -> None:
    """Merge as the maintainer, waiting out GitLab's asynchronous mergeability check."""
    maintainer = next(p for p in PEOPLE if "admin" in p.roles).gitlab_username
    for _ in range(30):
        response = gl.call(
            "PUT", f"/projects/{project_id}/merge_requests/{iid}/merge", sudo=maintainer
        )
        if response.is_success:
            return
        time.sleep(2)
    raise SystemExit(f"MR !{iid} in project {project_id} would not merge: {response.text[:300]}")


def _ensure_project_maintainer(gl: GitLab, project_id: int, username: str) -> None:
    user = gl.ok("GET", "/users", params={"username": username})[0]
    response = gl.call(
        "POST",
        f"/projects/{project_id}/members",
        json={"user_id": user["id"], "access_level": MAINTAINER},
    )
    if response.status_code == 409:
        gl.ok(
            "PUT",
            f"/projects/{project_id}/members/{user['id']}",
            json={"access_level": MAINTAINER},
        )
    elif not response.is_success:
        raise SystemExit(f"make {username} maintainer -> {response.status_code} {response.text}")


def _username(tag: str) -> str:
    person = next(p for p in PEOPLE if p.tag == tag)
    if person.gitlab_username is None:
        raise SystemExit(f"{person.name} has no gitlab_username in the roster")
    return person.gitlab_username


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} missing: run `python -m scripts.qa_org.seed_jira` first")
    loaded: dict[str, Any] = json.loads(STATE_FILE.read_text())
    return loaded


def main() -> None:
    tokens = env.require("OPENPROGRAM_QA_GITLAB_ADMIN_TOKEN", "OPENPROGRAM_GITLAB_TOKEN")
    admin_token = tokens["OPENPROGRAM_QA_GITLAB_ADMIN_TOKEN"]
    state = load_state()
    keys: dict[str, str] = state.get("jira_issues", {})
    if not {issue.summary for issue in ISSUES} & set(keys):
        raise SystemExit("no Jira issue keys in the state file: run seed_jira first")
    print("== tokens")
    if token_works(admin_token):
        print("  admin token valid")
    else:
        set_token("root", "openprogram-qa-admin", "api,sudo,admin_mode", admin_token)
    gl = GitLab(admin_token)
    try:
        users = ensure_users(gl, state)
        if token_works(tokens["OPENPROGRAM_GITLAB_TOKEN"]):
            print("  sync token valid")
        else:
            set_token(
                BOT,
                "openprogram-sync",
                "read_api,read_repository",
                tokens["OPENPROGRAM_GITLAB_TOKEN"],
            )
        group_id = ensure_group(gl, users)
        projects = ensure_projects(gl, group_id, state)
        push_work(gl, projects, keys, state)
    finally:
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        STATE_FILE.chmod(0o600)
    print(f"state written to {STATE_FILE}")


if __name__ == "__main__":
    main()
