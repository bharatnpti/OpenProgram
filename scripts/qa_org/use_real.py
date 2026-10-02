"""Write .env.qa: the shared .env plus the QA org's real Slack and Jira settings.

    uv run python -m scripts.qa_org.use_real                  # real integrations, catch-up paused
    uv run python -m scripts.qa_org.use_real --live-checkins  # ... and let it DM unprompted

Then (re)create only the docker backend and worker from it:

    docker compose -f docker-compose.yml -f docker-compose.qa.yml --env-file .env.qa \\
        up -d --no-deps --force-recreate backend worker

The shared .env is never modified. Other things read it at startup — a host
uvicorn started from this checkout, the test suite — and must keep getting the
mock demo; an earlier version of this script edited .env in place and silently
moved such a backend onto the real tenant. Back to the mock demo is the plain
``docker compose up -d --no-deps --force-recreate backend worker``.

The real org lives in its own tenant (``qa``), so the seeded ``demo`` tenant is
untouched. Tokens are copied from the shared secrets file into the gitignored
.env.qa; their values are never printed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx

from scripts.qa_org import env
from scripts.qa_org.roster import PEOPLE

BASE_ENV = Path(".env")
QA_ENV = Path(".env.qa")
TENANT_ID = "qa"

SECRET_KEYS = (
    "OPENPROGRAM_SLACK_BOT_TOKEN",
    "OPENPROGRAM_SLACK_SIGNING_SECRET",
    "OPENPROGRAM_JIRA_BASE_URL",
    "OPENPROGRAM_JIRA_EMAIL",
    "OPENPROGRAM_JIRA_API_TOKEN",
)


def admin_slack_id(token: str) -> str:
    """The dev principal is the roster admin, by their real Slack id."""
    admin = next(p for p in PEOPLE if "admin" in p.roles)
    response = httpx.get(
        "https://slack.com/api/users.lookupByEmail",
        params={"email": admin.slack_email},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    ).json()
    if not response.get("ok"):
        raise SystemExit(f"users.lookupByEmail for {admin.name} failed: {response.get('error')}")
    return str(response["user"]["id"])


def managed_values(*, live_checkins: bool) -> dict[str, str]:
    secrets = env.require(*SECRET_KEYS)
    return {
        # Off by default so switching mid-afternoon does not DM every member
        # within 15 minutes. NOTE: under DBOS this only stops the schedule from
        # being (re)created; one that already exists keeps firing until removed.
        "OPENPROGRAM_CHECKIN_RECONCILE_ENABLED": "true" if live_checkins else "false",
        # DMs the person a check-in asks something of. Off on main today; pinned
        # here because a branch in flight turns it on by default.
        "OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY": "true" if live_checkins else "false",
        "OPENPROGRAM_TENANT_ID": TENANT_ID,
        "OPENPROGRAM_CHAT_PROVIDER": "slack",
        "OPENPROGRAM_DIRECTORY_PROVIDER": "slack",
        "OPENPROGRAM_CHAT_SIMULATOR_ENABLED": "false",
        # Keep the acting-as picker: still local + dev auth, now over real people.
        "OPENPROGRAM_DEMO_MODE": "true",
        "OPENPROGRAM_ISSUE_TRACKER_PROVIDER": "jira",
        # GitLab comes later; until then VCS stays the fake adapter.
        "OPENPROGRAM_VCS_PROVIDER": "fake",
        "OPENPROGRAM_CALENDAR_PROVIDER": "fake",
        "OPENPROGRAM_DEV_PRINCIPAL_SUBJECT": admin_slack_id(secrets["OPENPROGRAM_SLACK_BOT_TOKEN"]),
        "OPENPROGRAM_DEV_PRINCIPAL_ROLES": "admin",
        **secrets,
    }


def render(base: list[str], values: dict[str, str]) -> list[str]:
    lines = list(base)
    seen: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            seen.add(key)
            lines[index] = f"{key}={values[key]}"
    lines.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live-checkins",
        action="store_true",
        help="let catch-up check-ins and cross-person notifications DM real members",
    )
    args = parser.parse_args()
    values = managed_values(live_checkins=args.live_checkins)
    QA_ENV.write_text("\n".join(render(BASE_ENV.read_text().splitlines(), values)) + "\n")
    QA_ENV.chmod(0o600)
    print(f"wrote {QA_ENV} (tenant {TENANT_ID}); {BASE_ENV} untouched. Recreate with:")
    print(
        "  docker compose -f docker-compose.yml -f docker-compose.qa.yml --env-file .env.qa "
        "up -d --no-deps --force-recreate backend worker"
    )


if __name__ == "__main__":
    main()
