"""Write .env.qa: the shared .env plus the QA org's real Slack and Jira settings.

    uv run python -m scripts.qa_org.use_real                  # real integrations, catch-up paused
    uv run python -m scripts.qa_org.use_real --live-checkins  # ... and let it DM unprompted
    uv run python -m scripts.qa_org.use_real --live-checkins --test-windows  # ... nudge within 1 h

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
from scripts.qa_org.roster import GITLAB_GROUP, PEOPLE

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


# Tenant defaults short enough that one test day sees the whole ladder: the
# developer is nudged after 15 min, the scrum master and then the manager 10 min
# apart, and an unanswered check-in closes after 30 min more. A member's own
# stored reply windows still win over these.
TEST_WINDOWS = {
    "OPENPROGRAM_CHECKIN_REPLY_WAIT_SECONDS": "900",
    "OPENPROGRAM_ESCALATION_SCRUM_MASTER_WAIT_SECONDS": "600",
    "OPENPROGRAM_ESCALATION_MANAGER_WAIT_SECONDS": "600",
    "OPENPROGRAM_CHECKIN_FINAL_REPLY_WAIT_SECONDS": "1800",
}


def managed_values(*, live_checkins: bool, test_windows: bool = False) -> dict[str, str]:
    secrets = env.require(*SECRET_KEYS)
    return {
        # Off by default so switching mid-afternoon does not DM every member
        # within 15 minutes; the worker deletes the schedule when it starts with
        # this off.
        "OPENPROGRAM_CHECKIN_RECONCILE_ENABLED": "true" if live_checkins else "false",
        # DMs the person a check-in asks something of. On by default in
        # settings; pinned off here unless a test needs real members DMed.
        "OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY": "true" if live_checkins else "false",
        "OPENPROGRAM_TENANT_ID": TENANT_ID,
        "OPENPROGRAM_CHAT_PROVIDER": "slack",
        "OPENPROGRAM_DIRECTORY_PROVIDER": "slack",
        "OPENPROGRAM_CHAT_SIMULATOR_ENABLED": "false",
        # Keep the acting-as picker: still local + dev auth, now over real people.
        "OPENPROGRAM_DEMO_MODE": "true",
        "OPENPROGRAM_ISSUE_TRACKER_PROVIDER": "jira",
        "OPENPROGRAM_CALENDAR_PROVIDER": "fake",
        "OPENPROGRAM_DEV_PRINCIPAL_SUBJECT": admin_slack_id(secrets["OPENPROGRAM_SLACK_BOT_TOKEN"]),
        "OPENPROGRAM_DEV_PRINCIPAL_ROLES": "admin",
        **secrets,
        **gitlab_values(),
        **(TEST_WINDOWS if test_windows else {}),
    }


def gitlab_values() -> dict[str, str]:
    """Self-hosted GitLab, once its credentials exist; the fake VCS until then.

    The root password is only read by compose (docker-compose.qa.yml) on the
    container's first boot; the token is the read-only one OpenProgram syncs with.
    """
    root_password = env.get("OPENPROGRAM_QA_GITLAB_ROOT_PASSWORD")
    token = env.get("OPENPROGRAM_GITLAB_TOKEN")
    if root_password is None or token is None:
        return {"OPENPROGRAM_VCS_PROVIDER": "fake"}
    return {
        "OPENPROGRAM_QA_GITLAB_ROOT_PASSWORD": root_password,
        "OPENPROGRAM_VCS_PROVIDER": "gitlab",
        "OPENPROGRAM_GITLAB_BASE_URL": "http://gitlab:8929/api/v4",
        "OPENPROGRAM_GITLAB_TOKEN": token,
        "OPENPROGRAM_GITLAB_NAMESPACE_ID": GITLAB_GROUP,
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
    parser.add_argument(
        "--test-windows",
        action="store_true",
        help="shorten reply, escalation and close-out waits so one day covers the nudge ladder",
    )
    args = parser.parse_args()
    values = managed_values(live_checkins=args.live_checkins, test_windows=args.test_windows)
    QA_ENV.write_text("\n".join(render(BASE_ENV.read_text().splitlines(), values)) + "\n")
    QA_ENV.chmod(0o600)
    print(f"wrote {QA_ENV} (tenant {TENANT_ID}); {BASE_ENV} untouched. Recreate with:")
    print(
        "  docker compose -f docker-compose.yml -f docker-compose.qa.yml --env-file .env.qa "
        "up -d --no-deps --force-recreate backend worker"
    )


if __name__ == "__main__":
    main()
