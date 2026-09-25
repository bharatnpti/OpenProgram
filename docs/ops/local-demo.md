# Local demo runbook

Runs the whole product on one machine with **no Slack, Jira, GitHub, or calendar
account** — the chat workspace, the issue tracker, the repos and the LLM are all
local stand-ins. The tenant is pre-loaded with a month of delivery history, so
every reporting screen has real data the moment it opens, and the console can be
switched between people to show each role its own view.

What is real and what is mocked:

| Piece | In this demo | Notes |
|---|---|---|
| Chat / DMs | built-in chat (`mock_slack`) | Real conversation store, threads per person, `/chat` screen |
| User directory | fixed 14-person roster | `backend/infra/adapters/directory/mock_slack.py` |
| Issue tracker, repos, calendar | `fake` adapters + seeded facts | Jira-shaped work items, PRs, commits live in the `facts` table |
| LLM | `scripts/mock_llm.py` | Rule-based, not a model: extracts blockers/ETA/requests from what you type |
| Status, rollups, risk, drift, briefs | **the real services** | Derived by the same code paths production uses |

Nothing in the seeded data is a hand-written RAG value. Check-ins, blocker
lifecycles and delivery facts are invented; every status dot, risk finding,
drift flag and brief on screen is then computed from them by
`RollupService`, `RiskService` and `NarrativeBriefService`.

---

## 0. The demo is opt-in

Persona switching is off unless `OPENPROGRAM_DEMO_MODE=true`, and the built-in
chat is off unless `OPENPROGRAM_CHAT_SIMULATOR_ENABLED=true`. Both default to
`false`, and `demo_mode` is refused outright unless `environment=local` **and**
`auth_provider=dev` — so no deployed console can be talked into acting as
someone by sending a header. The committed `.env` for this local stack turns
both on; `.env.example` leaves them off.

With `demo_mode` off the acting-as header is ignored (requests are served as the
configured dev principal), `/api/v1/auth/dev-users` returns an empty list, and
the console hides the person picker. With the chat flag off, the Chat nav item
and route disappear. `/api/v1/auth/status` reports both, so the UI only ever
offers what the backend will answer.

## 1. Bring the stack up

```bash
docker compose up -d postgres redis mock-llm temporal
```

```bash
docker compose up -d --no-deps backend worker
```

`--no-deps` skips the `litellm` gateway: `.env` points the backend straight at
`mock-llm`, so the gateway is not needed (and is not pullable on every network).

Wait for health, then confirm:

```bash
curl -s http://127.0.0.1:8000/ready
```

`llm_trace` reports `false` because Langfuse is profile-gated and not running.
Everything else should be `true`. To include Langfuse: `docker compose
--profile langfuse up -d`.

## 2. Load the demo tenant

```bash
docker compose exec -w /app backend python -m scripts.seed_demo_history --reset
```

Takes about a minute and prints a line per stage. `--reset` clears this tenant's
graph, history **and** the chat transcript first, so it is safe to re-run; without
it, writes are upserts and re-running is a no-op. `--skip-risk` drops the
risk/drift pass if you only need the graph and check-ins.

What it writes:

- 14 people across 4 pods, 3 projects, 5 workstreams, 14 work items, 14 tasks
- ~21 weekdays of check-ins per person — some answered, some deliberately not
- 9 blocker lifecycles, three of which resolve mid-window
- Jira/Git facts: state transitions, PRs (one aged 21 days), commits
- 7 cross-person requests in mixed states
- Then, over every seeded day: node rollups, risk findings, drift scans, briefs

The data is deliberately shaped to tell a story: **Payments is red**
(two long-running blockers plus one person who did not answer), **Storefront is
green**, Identity is amber on an aging review, and Insights is amber on a stale
spike.

## 3. Start the console

```bash
npm run dev --prefix frontend-v2
```

Open <http://localhost:5174>. The header carries an **acting-as picker**: pick
any of the 14 people and every request is re-issued as them, so each role's own
screens are one click apart. The picker only appears on a local dev-auth tenant;
under real auth the backend returns an empty roster and it disappears.

---

## Demo path

A ten-minute run that ends where it started, one level down.

**1. Executive — where is the programme?**
Acting as **Elena Fischer** → *Today*. The hero reads *Digital Platform Program
is at risk* with the specific reason, and the portfolio heat grid shows a real
red/amber/green spread. Nothing here was typed in; it is aggregated from
individual check-ins.

**2. Manager — which pod, and why?**
Switch to **Asha Rao** → *Delivery* → **Payments Pod**. Red, 6 members, *5 of 6
confirmed*, 3 open blockers. The one unconfirmed member is the point: silence is
not green.

**3. Signals — where does the story disagree with the facts?**
*Signals* shows 11 open risks and a 🍉 watermelon. Each card puts **owner says**
next to **signals say** — for example an owner reporting progress on a work item
that has had no pull request for days.

**4. Developer — what is actually asked of a person?**
Switch to **Kai Thompson** → *Today*. His status reads *unknown*: the check-in
went out this morning and he has not replied. His blocker is still carried
forward with its real age.

**5. Chat — answer as him and watch it propagate.**
*Chat* shows Kai's own thread with a month of history and this morning's
unanswered question. Type:

> Wired the 3-D Secure challenge flow. Still blocked on the sandbox credentials. ETA slips 2 days.

His status flips to **confirmed**, the ETA change is picked up as +2 days, and
the existing blocker is re-asserted — not duplicated, because the parser is
given the blockers already on file. Then clear it:

> Sandbox credentials came through so the sandbox credentials blocker is cleared. Step-up flow tested end to end. No blockers.

The blocker is resolved with reason `reported_resolved`, and a fresh check-in is
opened automatically for the follow-up.

**6. Coordination — who is waiting on whom.**
Switch back to **Asha Rao** → *Coordination*: open cross-person requests with
named counterparts, plus the generated daily/weekly/exec briefs.

---

## Notes and limits

- **The LLM is rules, not a model** *(default)*. `scripts/mock_llm.py` matches
  the phrases people actually type (`blocked on X`, `waiting for Y`,
  `slipping two days`, `need Priya to review`, `CHK-104 is merged`). It reuses
  the wording of a blocker already on file when you restate one, and reports a
  handle when you clear one. Phrase things naturally and it keeps up; invent
  new syntax and it will not.

- **Running against a real model.** For OpenAI EU, with the key read from a
  secrets file kept outside the repo (`$SECRETS_FILE` below) and never passed
  on the command line:

  ```bash
  OPENPROGRAM_LITELLM_API_KEY=$(grep '^OPENAI_API_KEY=' "$SECRETS_FILE" | cut -d= -f2-) \
      ./scripts/use-real-llm.sh gpt-4.1 https://eu.api.openai.com
  ```

  The base URL takes no `/v1` — the adapter appends `/v1/chat/completions`.
  Use a **standard chat model**: JSON-mode parses pin `temperature=0` for
  reproducibility, and reasoning models (`gpt-5.x`, `o3`, `o4`) accept only
  their default temperature, so they are refused. `gpt-4.1` is verified.

  A real model is better than the mock in two visible ways -- it personalises
  the check-in question ("Hi Kai, can you share today's progress on the 3-D
  Secure work?") and it asks a genuine follow-up when a reply leaves the ETA
  unanswered, leaving the status `partial` until you answer. Expect a couple of
  seconds per turn instead of instant, and note `/ready` reports
  `llm_provider=false`: that probe GETs `{base_url}/health/readiness`, which
  only a LiteLLM gateway serves.

  To go back to the mock, restore the four `OPENPROGRAM_LITELLM_*` lines in
  `.env` (or a backup of it) and recreate `backend` + `worker`.

- **Acting-as is not an auth bypass.** The `x-openprogram-dev-user` /
  `x-openprogram-dev-roles` headers are honoured only when
  `auth_provider == "dev"` **and** `environment == "local"`; settings already
  refuse the dev provider anywhere else. Role scoping still applies to whoever
  you are acting as — a developer opening *Signals* sees nothing, which is
  correct, not a bug.

- **Chat access is scoped.** Anyone may read and answer **their own** thread.
  Reading the whole tenant transcript, writing as someone else, or clearing
  history needs `manage_config`, so the roster sidebar on `/chat` only appears
  for an admin (Asha Rao is the seeded one).

- **Check-ins keep arriving.** The worker runs the real fan-out and reconcile
  crons, so new bot questions appear on schedule during a long demo. That is
  the product working, not drift in the seed.

- **Risk ages are ages at detection.** A finding carries the evidence from when
  it opened, so "no PR for 5 days" does not keep counting up while it stays
  open. The seeder assesses the last 12 weekdays.

- **"Today" adapts to the clock.** Seeded timestamps are folded into the part of
  the day that has actually happened, so a morning seed never dates events in
  the future where they would sort above anything typed live.

- **Editing the story.** Everything about the tenant — people, pods, work items,
  blockers, requests, per-person reply rates — is declared in
  `scripts/demo_roster.py`. Change it there and re-run the seeder; the rollups,
  risks and briefs re-derive themselves.

## Resetting

```bash
docker compose exec -w /app backend python -m scripts.seed_demo_history --reset
```

Puts the tenant back to the opening state, including Kai's unanswered check-in.
To tear the whole stack down (this drops the Postgres volume):

```bash
docker compose down -v
```
