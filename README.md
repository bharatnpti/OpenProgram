# OpenProgram

An agentic program-management system that keeps delivery status **continuously collected, reconciled, and rolled up** instead of assembled by hand in status meetings.

Instead of asking humans to report upward, OpenProgram asks developers a short conversational check-in in chat, cross-checks the answers against hard signals (Jira, Git, calendar), and derives every higher-level RAG indicator from those facts — so any red program dot can be drilled back down to the exact blocker on one person's task.

Three principles run through the whole system:

1. **One canonical graph, many views.** `program → project → workstream → pod → developer → task` is the single source of truth; each persona reads it through a different lens.
2. **Pull from humans, reconcile with systems.** Self-reported status is never trusted blindly — it is scored against delivery signals, and drift ("said done, no PR") is surfaced explicitly.
3. **Silence is never green.** A non-response becomes `unknown` with a lowered confidence score, not an assumed pass.

- Product concept and roadmap → [OpenProgramConcept.md](OpenProgramConcept.md)
- Business requirements / implemented feature set → [features.md](features.md)
- Engineering architecture and standards → [Architecture.md](Architecture.md)
- Low-level designs → [docs/lld/](docs/lld/)
- Slack setup → [docs/ops/slack-setup.md](docs/ops/slack-setup.md)
- Local demo with a populated tenant → [docs/ops/local-demo.md](docs/ops/local-demo.md)
- Demo videos, English and Hindi → [Demo videos](#demo-videos)

---

## Demo videos

A narrated walkthrough of one weekend on real tools: a Slack workspace, a Jira Cloud site and a self-hosted GitLab server. The eleven people in it are AI agents playing the team of a fictional company, Acme Digital, each signed in from its own browser; every message, ticket and merge request on screen is real.

| Video | Length | Subtitles |
| --- | --- | --- |
| [▶ English](https://github.com/bharatnpti/OpenProgram/releases/download/demo-videos-2026-10/openprogram-demo-en.mp4) | 10:16 · 124 MB | [English](https://github.com/bharatnpti/OpenProgram/releases/download/demo-videos-2026-10/openprogram-demo-en.srt) |
| [▶ Hindi (Hinglish)](https://github.com/bharatnpti/OpenProgram/releases/download/demo-videos-2026-10/openprogram-demo-hi.mp4) | 12:20 · 137 MB | [Hindi](https://github.com/bharatnpti/OpenProgram/releases/download/demo-videos-2026-10/openprogram-demo-hi.srt) · [English](https://github.com/bharatnpti/OpenProgram/releases/download/demo-videos-2026-10/openprogram-demo-hi.en.srt) |

Each link downloads the MP4 (1080p, with its subtitles built in); the [release page](https://github.com/bharatnpti/OpenProgram/releases/tag/demo-videos-2026-10) lists every file with its checksum. Both videos show the same scenes, and only the narration differs. Each feature is named before it is shown:

- check-in questions written from each person's own tickets and merge requests, and follow-ups that name the exact ticket;
- Jira updated from a check-in, asking the person first by default or automatically for people who opted in, with every change in an audit log;
- a wait on another team tracked as a dependency that clears itself when that work merges, and review requests routed to the reviewer;
- a ticket moved to Done only when its merge request has merged, and only by its owner's own check-in;
- reminders and escalation to the scrum master and manager when someone does not reply, with the silence recorded as inferred, never green;
- what people say checked against GitLab, such as an "on track" ticket whose merge request has gone quiet;
- the manager, scrum master, product owner and executive views, and asking the graph a question in plain language.

---

## Screens

The console lives in [`frontend-v2/`](frontend-v2) — five top-level destinations plus admin, all wired to the real backend. Screenshots below are from a local stack running the seeded demo tenant ([docs/ops/local-demo.md](docs/ops/local-demo.md)); on a fresh, empty tenant most indicators read `unknown`, which is the honest "no data yet" state rather than a rendering bug.

### Today — persona home

Every role lands here. Developers get their check-in to confirm or correct plus a ranked focus list; managers and executives get a portfolio-wide read on health, momentum, and what changed.

![Today, executive persona](docs/images/today-exec.png)

The same screen for a developer: the check-in to confirm or correct, the
blocker carried forward with its real age, and a focus list ranked by urgency.

![Today, developer persona](docs/images/today-dev.png)

And for a scrum master: who has checked in across their own pods, and how long
each blocker has been open.

![Today, scrum master persona](docs/images/today-sm.png)

### Delivery — graph explorer

Walk the program graph and drill into any project, workstream, or pod. Selection is URL-synced (`/delivery/:kind/:id`) so any view is linkable.

![Delivery graph explorer](docs/images/delivery.png)

### Signals — risks, drift, and flow

Merges portfolio risks, flow metrics, and the activity feed into one ranked stream: stale work items, ageing pull requests, features with no pull request, watermelon status (green over red), and reported-vs-actual drift.

![Signals](docs/images/signals.png)

### Coordination — requests, briefs, and ask-the-graph

Cross-person requests board (the person a check-in asks something of is sent a DM by default; `OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY=false` turns that off), scheduled narrative briefs, and a natural-language query box that answers from the graph rather than from a free-form LLM guess.

![Coordination](docs/images/coordination.png)

### Chat — the check-in conversation

Stands in for the chat workspace when no Slack is connected: one thread per
person, the bot's daily question, and a composer whose reply is parsed into
structured status. Everyone sees their own thread; a config manager also gets
the roster and can open anyone's.

![Check-in chat](docs/images/chat.png)

### Acting as another person

On a local dev-auth tenant the header carries a person picker. Selecting someone
re-issues every request as them, so each role's own screens are one click apart
without a login per person — see [docs/ops/local-demo.md](docs/ops/local-demo.md).
The picker is absent under any real auth provider, and role scoping still applies
to whoever you are acting as.

### Admin — runtime configuration

Full CRUD over the hierarchy, directory onboarding, graph links, assignments, identity links, escalation contacts, write-back consent, per-member check-in days and reply windows, and data-source sync status.

![Admin configuration](docs/images/admin.png)

### Command palette

`⌘K` anywhere jumps to a screen or any entity in the graph.

![Command palette](docs/images/command-palette.png)

> There are **two** frontends in this repo. [`frontend/`](frontend) is the original console (dev server on **5173**): per-persona dashboard routes (`/me`, `/sm`, `/po`, `/mgr`, `/exec`) plus separate pages for pods, projects, workstreams, flow, portfolio, risks, cross-person requests, admin, and the mock-Slack simulator. [`frontend-v2/`](frontend-v2) is the IA redesign shown above (dev server on **5174**), collapsing all of that into `/today`, `/delivery`, `/signals`, `/coordination`, plus `/chat` in the nav and `/admin` behind the avatar menu (`/sim` redirects to `/chat`). CI and `make verify` lint, typecheck, format-check and build both.

---

## Architecture

Clean/hexagonal, enforced by `import-linter` in CI: the domain is pure, dependencies point inward, and every external system sits behind a port.

```
backend/
  core/
    domain/          # entities, value objects, rules — NO external imports
    application/     # use cases, agents (LangGraph nodes), orchestration
    ports/           # Protocol/ABC interfaces (chat, tracker, vcs, llm, repos)
  infra/
    adapters/        # chat/, directory/, jira/, github/, gitlab/, calendar/,
                     # llm/, auth/, secrets/, workflows/ (dbos.py + temporal.py)
    persistence/     # Postgres (+ Timescale) repositories, Alembic migrations
    workflows/       # provider-neutral workflow payloads and operations
    registry.py      # config -> adapter wiring (composition root)
  api/               # FastAPI routers, DTOs, dependency wiring
  config/            # pydantic-settings
  tests/             # unit/, contract/, integration/, bdd/
frontend/            # original React/Vite console (port 5173)
frontend-v2/         # redesigned console (port 5174)
```

**Dependency rule:** `api → application → domain`, and `infra → ports`. The core never imports a vendor SDK, `infra`, `api`, or `config.settings`; provider values thread through constructors. A guard test also bans the literal string `slack` inside `core`/`api` to keep the core provider-neutral (`chat_external_id`, not `slack_user_id`).

One port per capability, many swappable adapters:

| Port | Capability | Adapters |
|---|---|---|
| `ChatProvider` | DM / threaded conversation | Slack, mock-Slack simulator, fake |
| `DirectoryProvider` | user directory sync | Slack, fake |
| `IssueTracker` | issues, transitions, comments | Jira |
| `VcsProvider` | repos, commits, PRs | GitHub, GitLab |
| `CalendarProvider` | availability, PTO, timezone | Google |
| `LlmProvider` | model calls | LiteLLM |
| `GraphRepository` / `RollupRepository` / … | persistence | Postgres, in-memory |

### Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Agents | LangGraph |
| LLM gateway | LiteLLM (Langfuse tracing) |
| Workflow engine | DBOS by default, Temporal selectable via config |
| Storage | PostgreSQL (+ TimescaleDB), Redis |
| Frontend | React 19, TypeScript, Vite, Tailwind |
| Observability | OpenTelemetry, Prometheus, Grafana |
| Tooling | uv, ruff, mypy, import-linter, pytest (+ pytest-bdd), Playwright |

Any workflow change must be mirrored in **both** engines — `infra/adapters/workflows/dbos.py` and `temporal.py` — with JSON-native payloads (ISO strings, no `datetime` objects).

---

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env
```

Bring up the default stack — Postgres, Redis, LiteLLM, a mock LLM, the API, and the workflow worker (Langfuse, Temporal, and the observability stack are opt-in compose profiles):

```bash
docker compose up -d
```

Apply migrations:

```bash
make migrate
```

The API is then on <http://127.0.0.1:8000> (`/health`, `/ready`, `/metrics`, `/docs`). Start a console against it:

```bash
cd frontend-v2 && npm install && npm run dev
```

Open <http://127.0.0.1:5174>. In `local` environment with `auth_provider=dev` you are an unauthenticated admin and can switch persona from the avatar menu.

A fresh database has no hierarchy, so every indicator reads `unknown` until you populate the graph — either from the **Admin → Directory** screen (sync users from the chat directory, then import them as members) or through the `/config/*` API, then link projects → programs and pods → projects. The fastest end-to-end proof that needs no infra at all is `make phase1-smoke`, which builds a graph, runs a check-in, a nudge, a sync, and a rollup entirely in memory with fake providers.

If the backend is published on a different host port (via `OPENPROGRAM_BACKEND_PORT_BINDING`), point the dev server at it:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8001 npm run dev
```

To run the API on the host instead of in a container, use `make api` (uvicorn with reload) plus `make worker`.

### Driving the check-in loop without Slack

Point chat and directory at the built-in chat and restart the backend:

```bash
OPENPROGRAM_CHAT_PROVIDER=mock_slack
OPENPROGRAM_DIRECTORY_PROVIDER=mock_slack
OPENPROGRAM_CHAT_SIMULATOR_ENABLED=true
```

The `/chat` screen is then the chat workspace: one thread per person, the bot's
check-in question, and a composer that files a reply against it — replies travel
the same webhook correlation path as real chat, so the parsed status lands in the
rollup. Anyone may read and answer their own thread; the roster sidebar, the
tenant-wide transcript and history reset need `manage_config`. The
`/test/chat-simulator/*` endpoints are the same surface for scripts. Details in
[mock-slack.md](mock-slack.md); for real Slack, follow
[docs/ops/slack-setup.md](docs/ops/slack-setup.md).

### Demoing the whole product on one machine

For a populated tenant — 14 people, a month of check-ins, blockers, risks and
briefs, plus an acting-as picker that switches the console between people — see
**[docs/ops/local-demo.md](docs/ops/local-demo.md)**. Short version:

```bash
docker compose exec -w /app backend python -m scripts.seed_demo_history --reset
```

The seed invents only the raw inputs (check-ins, blocker lifecycles, Jira/Git
facts); every status dot, risk finding and brief on screen is then derived by the
real rollup, risk and brief services.

---

## Configuration

All settings are `OPENPROGRAM_`-prefixed pydantic-settings, documented in [.env.example](.env.example). The ones that change behaviour most:

| Variable | Purpose |
|---|---|
| `OPENPROGRAM_ENVIRONMENT` | `local` enables dev conveniences; anything else hard-fails on dev auth or a default secret key |
| `OPENPROGRAM_RUNTIME_MODE` | `container` (Postgres/Redis) or `memory` (in-process fakes) |
| `OPENPROGRAM_AUTH_PROVIDER` | `dev` (unauthenticated admin, local only) or `oidc_bff` (cookie-based OIDC BFF) |
| `OPENPROGRAM_WORKFLOW_PROVIDER` | `dbos` or `temporal` |
| `OPENPROGRAM_CHAT_PROVIDER` | `slack`, `mock_slack`, or `fake` |
| `OPENPROGRAM_ISSUE_TRACKER_PROVIDER` / `VCS_PROVIDER` / `CALENDAR_PROVIDER` | `jira` / `github`\|`gitlab` / `google` |
| `OPENPROGRAM_CHECKIN_FANOUT_CRON` | when daily check-ins go out — one schedule for the whole tenant; each member's check-in days are honoured, but a per-member check-in time is stored and not used |
| `OPENPROGRAM_CONVERSATION_RETENTION_DAYS` | retention for raw chat conversation history |

Jira write-back is **default-deny behind three independent gates**: a tenant flag (`jira_writeback_enabled`), the `WRITE_ISSUE_TRACKER` capability, and per-developer consent (`always_ask` / `auto_apply` / `never`). Only `writeback_service.py` may call the write path, and every applied change is audited and revertible.

---

## Development

```bash
make lint          # ruff check + format check + mypy + import-linter
make test          # pytest (unit + contract + bdd)
make integration   # container-backed integration tests (needs the stack up)
make phase1-smoke  # phase-1 flow end to end, in memory with fake providers
make smoke         # phase-0 smoke: readiness + a real LLM trace landing in Langfuse
make verify        # everything above, plus lint/build of both frontends and OpenAPI drift
```

`make smoke` asserts a Langfuse trace, so it needs that profile running: `docker compose --profile langfuse up -d`.

Frontend:

```bash
make frontend-v2-install frontend-v2-lint frontend-v2-build   # the console
make frontend-install frontend-lint frontend-build            # original app
```

**OpenAPI contract:** whenever an API route or DTO changes, regenerate the schema and both clients and commit them — CI runs the same drift gate.

```bash
make openapi-check   # rewrites openapi.json + generated.ts in frontend/ and frontend-v2/, fails while they differ from the index
```

Install both apps first (`make frontend-install frontend-v2-install`) so `npx prettier` resolves to each app's pinned local binary — a global prettier produces spurious diffs.

### Tests

| Suite | What it covers |
|---|---|
| `backend/tests/unit` | services, agents, parsing, settings, architecture boundaries |
| `backend/tests/contract` | each adapter against its port contract (Slack, Jira, GitHub, GitLab, Google Calendar) |
| `backend/tests/integration` | container-backed flows (`OPENPROGRAM_RUN_INTEGRATION=1`) |
| `backend/tests/bdd` | Gherkin scenarios for check-in, reply parsing, reliability, access control; some need the running simulator/LLM |
| `make ui-bdd` | Playwright-backed Mock Slack UI scenarios (`OPENPROGRAM_RUN_UI_BDD=1`) |

CI (`.github/workflows/ci.yml`) runs backend lint+types+tests, integration, lint/typecheck/format-check/build of both frontends, the OpenAPI drift gate, and the image build on every PR.

---

## Repository map

| Path | Contents |
|---|---|
| [backend/](backend) | FastAPI service, agents, adapters, migrations, tests |
| [frontend/](frontend) | original React console (port 5173) |
| [frontend-v2/](frontend-v2) | redesigned console (port 5174) |
| [infra/](infra) | container, Prometheus, Grafana, OTel, and LiteLLM configuration |
| [scripts/](scripts) | mock LLM and local helper scripts |
| [docs/lld/](docs/lld) | low-level designs per seam |
| [docs/ops/](docs/ops) | Slack setup and the local demo |
| [docker-compose.yml](docker-compose.yml) | full local stack with opt-in profiles |
| [Makefile](Makefile) | every task above |
