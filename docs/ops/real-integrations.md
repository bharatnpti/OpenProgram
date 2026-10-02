# Real-integration QA org

The [local demo](local-demo.md) runs on mocks. This runbook points the same local
stack at a **real Slack workspace, a real Jira Cloud site and a self-hosted
GitLab**, populated with an enterprise-shaped org, so behaviour the
mocks hid shows up.

| Piece | Here | Notes |
|---|---|---|
| Chat + directory | Slack Free workspace, bot from [`infra/slack/app-manifest.yaml`](../../infra/slack/app-manifest.yaml) | Replies arrive over a tunnel, see §4 |
| Issue tracker | Jira Cloud Free (10-user cap) | Company-managed scrum projects |
| VCS | GitLab CE in docker (`docker-compose.qa.yml`), http://localhost:8929 | ~3 GB RAM; synced read-only by `openprogram-bot` |
| LLM | whatever `.env` already uses | unchanged by the switch |
| Tenant | `qa` | the seeded `demo` tenant is left untouched |

Everything about the org is declared in [`scripts/qa_org/roster.py`](../../scripts/qa_org/roster.py).

## The org

11 people, 5 pods, 3 projects, 28 Jira issues plus epics. Shaped to hit the
cases a single-pod-per-person demo never does:

| Person | Role | Pods | Why they are there |
|---|---|---|---|
| Elena Fischer | exec | — | exec with no pod and **no Jira seat** |
| Asha Rao | mgr, admin | Payments, Storefront, Identity, Platform (manager) | owns the workspace, site and tokens; the dev principal |
| Mina Patel | po | Payments, Storefront | PO over two pods carved out of **one** Jira project |
| Hana Kobayashi | po | Identity, Data | Asia/Tokyo |
| Ira Novak | sm | Payments, Identity | owns no tickets |
| Liam Chen | dev | Payments | single-pod baseline |
| Noah Weber | dev | Payments, Identity | **split across two projects**, heaviest load |
| Zoe Almeida | dev | Storefront, Payments | **two pods in the same project**, in progress in both |
| Omar Haddad | dev | Platform, Identity, Data | **shared SRE in three pods**, Asia/Kolkata |
| Sofia Bergmann | dev | Identity, Storefront | QA across two projects |
| Raj Iyer | dev | Data | **Jira email differs from Slack email** |

| Pod | Project(s) | Jira scope |
|---|---|---|
| Payments | Checkout (CHK) | `component = Payments` |
| Storefront | Checkout (CHK) | `component = Storefront` |
| Identity | Identity (IDP) | whole project |
| Data | Insights (INS) | whole project; **no manager** |
| Platform | Checkout **and** Identity | `labels = platform` — so some issues sit in two pods |

Also seeded: unassigned issues, a backlog outside the sprint, one active
two-week sprint per project.

GitLab group `acme` holds six repositories (`platform-libs` is shared by both
Checkout and Identity). Seven people have accounts, with logins that differ
from their Slack handles. `GIT_WORK` in the roster has 14 pieces of work, one
per signal: MRs that are open, draft or merged; a merged fix whose Jira issue
still says In Progress; a branch with commits and no MR; Payments-pod work in
the Storefront pod's repo; and a direct push to `main` by an SRE with maintainer
rights. Branches and MR titles carry the Jira key.

## 1. Accounts (manual, once)

Accounts are created by a person, not a script. All addresses are
plus-addresses on one mailbox, `<mailbox>+<firstname>@<domain>` (Raj's Jira
address uses `+riyer`). Disposable-mail services do not work: Atlassian refuses
them with "Email is blocked".

1. Slack: create the workspace as Asha, create the app from the manifest, install
   it, invite the other 10, accept each invite in a private window with
   email + password (not "Continue with Google", which binds the bare mailbox).
2. Jira: sign up for Jira Free as Asha, create a classic API token.
3. Put the values in `~/.config/oneai/secrets.env`:
   `OPENPROGRAM_QA_MAIL_BASE`, `OPENPROGRAM_SLACK_BOT_TOKEN`,
   `OPENPROGRAM_SLACK_SIGNING_SECRET`, `OPENPROGRAM_JIRA_BASE_URL`,
   `OPENPROGRAM_JIRA_EMAIL`, `OPENPROGRAM_JIRA_API_TOKEN`.

```bash
uv run python -m scripts.qa_org.check_tokens
```

## 2. Fill Jira

```bash
uv run python -m scripts.qa_org.seed_jira --dry-run
```

```bash
uv run python -m scripts.qa_org.seed_jira
```

Idempotent; re-run to fill gaps. Account ids, issue keys, board and sprint ids
go to `~/.config/oneai/openprogram-qa-state.json`. Atlassian throttles
**invitations** on a new site (429 with `Retry-After`, sometimes a hang or 504)
after about six; the seeder waits, then skips the rest and still creates
everything else. Invited users are assignable before they accept.

## 3. Start and fill GitLab

`use_real` (§4) needs three generated values in the secrets file first:
`OPENPROGRAM_QA_GITLAB_ROOT_PASSWORD`, `OPENPROGRAM_QA_GITLAB_ADMIN_TOKEN` and
`OPENPROGRAM_GITLAB_TOKEN` (any strong values; the tokens start `glpat-`).

```bash
uv run python -m scripts.qa_org.use_real
```

```bash
docker compose -f docker-compose.yml -f docker-compose.qa.yml --env-file .env.qa up -d gitlab
```

```bash
uv run python -m scripts.qa_org.seed_gitlab
```

Boot takes a few minutes (wait until http://localhost:8929/users/sign_in
answers). The seeder installs both tokens with `gitlab-rails runner`, since the
API cannot create a token with a chosen value. The admin token (root, `api` +
`sudo`) seeds, and `Sudo` makes each commit and MR appear as its author.
OpenProgram syncs with the other token: `openprogram-bot`, Reporter on the
group, `read_api` only, so writes are refused. It is idempotent like the Jira
seeder. Sign in to the UI as `root` with the generated password.

## 4. Switch the stack and load the org

```bash
uv run python -m scripts.qa_org.use_real
```

```bash
docker compose -f docker-compose.yml -f docker-compose.qa.yml --env-file .env.qa up -d --no-deps --force-recreate backend worker
```

```bash
uv run python -m scripts.qa_org.seed_openprogram
```

`use_real` writes `.env.qa` (gitignored): the shared `.env` plus tenant `qa`,
real Slack and Jira, GitLab once its tokens exist, and the dev principal set to Asha's real Slack id. It
**never edits `.env`**: anything else that reads `.env` at startup — a host
`uvicorn` run from this checkout, the test suite — must keep getting the mock
demo. (An earlier version edited `.env` in place and moved a parallel session's
host backend onto the real tenant.) `docker-compose.qa.yml` points only the
docker backend and worker at `.env.qa`.

`seed_openprogram` goes through the `/config/*` API only and links each
person's Slack id, Jira account and GitLab login. Raj's Jira account is left
unlinked on purpose; `--link-raj` links it.

**Check-in catch-up.** `use_real` sets `CHECKIN_RECONCILE_ENABLED=false`, but
under DBOS that only stops the schedule being *created*: one that already exists
in `dbos.workflow_schedules` keeps firing (it DMed all 11 members once on
2026-10-02). Pause it explicitly, and resume it when a test needs it:

```bash
docker compose exec -T worker python -c "import os; from dbos import DBOSClient; c = DBOSClient(system_database_url=os.environ['OPENPROGRAM_DATABASE_URL']); c.pause_schedule('openprogram-checkin-reconcile'); c.destroy()"
```

The 09:30 weekday fan-out (`openprogram-checkin-fanout`, cron in UTC) stays
active.

`use_real` also pins `CROSS_PERSON_AUTO_NOTIFY=false` (a branch in flight turns
it on by default; it DMs whoever a check-in asks something of).
`--live-checkins` turns both on.

## 5. Inbound replies

Slack posts replies to `/webhooks/chat/slack`, which must be public HTTPS.
Under dev auth every other route is an unauthenticated admin, so never tunnel
port 8000 directly — tunnel the gate, which forwards exactly
`POST /webhooks/chat/<provider>` and 404s the rest:

```bash
uv run python -m scripts.qa_org.webhook_gate
```

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8787
```

Then in the Slack app: **Event Subscriptions → On → Request URL**
`https://<tunnel>/webhooks/chat/slack` → **Subscribe to bot events** `message.im`
→ Save. The quick-tunnel hostname changes on every restart; update the URL each
time.

## Back to the mock demo

`.env` was never changed, so recreating without the override is enough:

```bash
docker compose up -d --no-deps --force-recreate backend worker
```

## Found while setting up

Fixed on this branch:

- Nobody's Jira work was found. Jira indexes assignments by `accountId`; the
  identity link's `jira_email` was never read, lookups fell back to the Slack id
  (Jira answers that with an empty 200), and the agent's Jira tool ignored the
  link entirely. Now auto-match and the status lookup resolve `jira_email` ->
  `accountId` through a new read-only `IssueTracker.find_user_by_email`, store
  it on the link, and the tool uses it.
- Jira sync made every assignee a second developer node keyed by `accountId`,
  so the member's own views never saw the issue. Linked assignees now land on
  the member.
- Directory sync imported Slackbot (`USLACKBOT` is not flagged `is_bot`).
- `/config/members/unmapped` ignored members with no Jira account. With a real
  tracker configured it now flags them; the admin page says which link is
  missing.
- Git sync made every author a new developer node: MR authors by login, commit
  authors by email. That would have meant up to three "people" per developer.
  `vcs_username` was never read. MR logins now map through `vcs_username` and
  commit emails through the member's directory email.

Open:

- Unknown git authors still become developer nodes, and so personas. GitLab's
  root, which authored each repo's initial README, shows up as "Administrator";
  CI bots and service accounts would too.
- Only default-branch commits are synced, so a branch's commits are invisible
  until it is merged. A branch with no MR is visible only as a missing MR.
- `CHECKIN_RECONCILE_ENABLED=false` does not remove an existing DBOS schedule
  (above).
- `test_cross_person_service.py::test_registry_routes_slack_thread_reply_by_notify_message_id_first`
  passes on a clean checkout and fails when the checkout's `.env` carries real
  Slack settings: something in that path still reads the local `.env`.
