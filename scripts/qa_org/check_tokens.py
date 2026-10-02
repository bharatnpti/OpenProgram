"""Verify the QA org's Slack and Jira credentials, and who has joined so far.

    uv run python -m scripts.qa_org.check_tokens

Prints identities, scopes and membership; never a token value.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import PEOPLE

SLACK_SCOPES = {"chat:write", "im:write", "im:history", "users:read", "users:read.email"}


def check_slack() -> bool:
    token = env.get("OPENPROGRAM_SLACK_BOT_TOKEN")
    secret = env.get("OPENPROGRAM_SLACK_SIGNING_SECRET")
    print("== Slack")
    if token is None:
        print("  OPENPROGRAM_SLACK_BOT_TOKEN is empty")
        return False
    print(f"  signing secret: {'present' if secret else 'MISSING'}")
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url="https://slack.com/api", headers=headers, timeout=20) as client:
        auth = client.post("/auth.test")
        body: dict[str, Any] = auth.json()
        if not body.get("ok"):
            print(f"  auth.test failed: {body.get('error')}")
            return False
        granted = set(filter(None, auth.headers.get("x-oauth-scopes", "").split(",")))
        print(f"  workspace: {body.get('team')} ({body.get('url')})  bot user: {body.get('user')}")
        missing = SLACK_SCOPES - granted
        scopes = "all granted" if not missing else "MISSING " + ", ".join(sorted(missing))
        print(f"  scopes: {scopes}")

        members: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = client.get("/users.list", params={"limit": "200", "cursor": cursor}).json()
            if not page.get("ok"):
                print(f"  users.list failed: {page.get('error')}")
                return False
            members.extend(page.get("members", []))
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

    humans = [m for m in members if not m.get("is_bot") and not m.get("deleted")]
    humans = [m for m in humans if m.get("id") != "USLACKBOT"]
    by_email = {str(m.get("profile", {}).get("email", "")).lower(): m for m in humans}
    joined = 0
    for person in PEOPLE:
        member = by_email.get(person.slack_email.lower())
        if member is None:
            print(f"  [ pending ] {person.name:<16} {person.slack_email}")
            continue
        joined += 1
        real = member.get("profile", {}).get("real_name") or member.get("real_name")
        note = "" if real == person.name else f"  (Slack name is '{real}')"
        print(f"  [ joined  ] {person.name:<16} {member.get('id')}{note}")
    print(f"  {joined}/{len(PEOPLE)} roster people in the workspace, {len(humans)} humans total")
    return not missing and joined == len(PEOPLE)


def check_jira() -> bool:
    print("== Jira")
    base_url = env.get("OPENPROGRAM_JIRA_BASE_URL")
    email = env.get("OPENPROGRAM_JIRA_EMAIL")
    token = env.get("OPENPROGRAM_JIRA_API_TOKEN")
    if not (base_url and email and token):
        print("  OPENPROGRAM_JIRA_BASE_URL / _EMAIL / _API_TOKEN not all set")
        return False
    with httpx.Client(base_url=base_url.rstrip("/"), auth=(email, token), timeout=20) as client:
        me = client.get("/rest/api/3/myself")
        if me.status_code != 200:
            print(f"  /myself -> HTTP {me.status_code}: {me.text[:200]}")
            return False
        myself = me.json()
        print(f"  site: {base_url}  as: {myself.get('displayName')} ({myself.get('accountId')})")
        perms = client.get(
            "/rest/api/3/mypermissions", params={"permissions": "ADMINISTER,CREATE_PROJECT"}
        ).json()
        admin = perms.get("permissions", {}).get("ADMINISTER", {}).get("havePermission")
        print(f"  Jira admin: {'yes' if admin else 'NO - seeding needs site admin'}")
        projects = client.get("/rest/api/3/project/search").json().get("values", [])
        print(f"  projects: {', '.join(p['key'] for p in projects) or 'none yet'}")
        users = client.get("/rest/api/3/users/search", params={"maxResults": "100"}).json()
        humans = [u for u in users if u.get("accountType") == "atlassian" and u.get("active")]
        print(f"  active human users: {len(humans)} (Jira Free allows 10)")
    return bool(admin)


def main() -> None:
    slack_ok = check_slack()
    jira_ok = check_jira()
    sys.exit(0 if slack_ok and jira_ok else 1)


if __name__ == "__main__":
    main()
