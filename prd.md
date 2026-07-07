---
name: OpenProgram Phase Plan
overview: A phase-wise delivery plan for the OpenProgram agentic program-management system, broken into epics and user stories across a foundation phase plus the four roadmap phases (Sense, Reconcile, Predict, Coach).
todos:
  - id: phase-0
    content: "Phase 0 Foundations: graph data model, RBAC/SSO, integration framework + secrets, service skeleton and CI/CD"
    status: pending
  - id: phase-1
    content: "Phase 1 Sense: read-only integrations, daily Status Collector bot, roll-up engine v1, persona views v1"
    status: pending
  - id: phase-2
    content: "Phase 2 Reconcile: hard-signal ingestion, reconciliation + confidence scoring, drift detection, Jira write-back"
    status: pending
  - id: phase-3
    content: "Phase 3 Predict: risk/blocker engine, milestone slip prediction, dependency-stall detection, smart escalation, advanced viz"
    status: pending
  - id: phase-4
    content: "Phase 4 Coach: sentiment/burnout signals, load balancing, auto standup/retro, ask-anything NL query, knowledge memory"
    status: pending
isProject: false
---

# OpenProgram — Phase-wise Plan (Epics and Stories)

This expands the roadmap in [OpenProgramConcept.md](OpenProgramConcept.md) into a delivery backlog. Structure: **Phase to Epic to Story**. Stories use `As a <persona>, I want <capability>, so that <value>` with brief acceptance hints. Personas: Dev, PO, SM (Scrum Master), Mgr (Manager), Exec, Admin.

Phase dependency flow:

```mermaid
graph LR
    P0[Phase 0 Foundations] --> P1[Phase 1 Sense]
    P1 --> P2[Phase 2 Reconcile]
    P2 --> P3[Phase 3 Predict]
    P3 --> P4[Phase 4 Coach]
```

---

## Phase 0 — Foundations and Platform

Goal: build the skeleton everything else hangs off of — the hexagonal service, the Graph of Truth, the ports/adapters seam, and the delivery pipeline. No user-facing value yet, but it unblocks all later phases and locks in the architecture from [Architecture.md](Architecture.md).

### Phase 0 outcomes (what "done" looks like)
- A running FastAPI service and React shell scaffolded in the hexagonal layout (`core/`, `infra/`, `api/`), with the dependency rule enforced in CI (`import-linter`).
- The Graph of Truth (Program → Project → Pod → Developer → Task) persisted in Postgres + Apache AGE, with **time-bounded** `dev ↔ pod ↔ project` mappings and an **append-only fact/event log**.
- At least one capability fully proven through the ports-and-adapters seam end-to-end (a `ChatProvider` with a real adapter + an in-memory fake) and its **shared contract test suite** green.
- Temporal wired and running a trivial scheduled workflow; LiteLLM + Langfuse reachable; OpenTelemetry traces flowing.
- One-command local bring-up (`docker compose`), migration-backed graph storage, and green CI (ruff, mypy --strict, import-linter, unit + contract + integration tests with Testcontainers).

### Open decisions to lock before/at start (blockers)
These are the open decisions called out at the end of this doc; Phase 0 cannot finish the relevant epics until they're settled.

| Decision | Default for Phase 0 | Affects |
|---|---|---|
| Primary chat platform (Teams vs Slack) | Slack adapter first (Teams via same port later) | Epic 0.3 |
| Jira Cloud vs Server/DC | Jira Cloud | Epic 0.3 (Phase 1 sync) |
| Single-tenant vs multi-tenant | Single-tenant now; carry a `tenant_id` column/seam so multi-tenant is additive | Epics 0.1, 0.2, 0.3 |
| Identity / SSO | **Deferred** per [Architecture.md](Architecture.md). Phase 0 ships an auth *seam* (stub principal + role enum), not real SSO | Epic 0.2 |

---

### Epic 0.0 — Repo, environments, and developer workflow
Goal: a repeatable, opinionated dev environment so every later story starts from a green baseline.

**Story 0.0.1** — As Admin, I want the monorepo scaffolded in the layout from [Architecture.md](Architecture.md) §4 so that code has a home.
- Tasks: create `backend/{core/{domain,application,ports},infra/{adapters,persistence,workflows,registry.py},api,config,tests}` and `frontend/`; add `pyproject.toml` (Python 3.12), `uv`/`pip-tools` lockfile, `package.json` (Vite + TS).
- Acceptance: tree matches §4; `import-linter` contract file present; empty packages import cleanly.

**Story 0.0.2** — As a Developer, I want one-command local bring-up so that I can run the whole stack.
- Tasks: `docker-compose.yml` for Postgres (with AGE + Timescale + pgvector), Redis, Temporal, Langfuse, OTel collector + Grafana/Prometheus; `Makefile`/`justfile` targets (`up`, `migrate`, `test`, `lint`).
- Acceptance: `make up` boots all deps; health endpoint returns 200; `make migrate` prepares the database schema.

**Story 0.0.3** — As Admin, I want config + secrets via `pydantic-settings` so that the app is 12-factor and fails fast.
- Tasks: `config/settings.py` with typed settings (DB URLs, Redis, Temporal, LiteLLM, provider selectors like `chat_provider=slack`); `.env.example`; boot-time validation.
- Acceptance: missing required env fails fast at startup with a clear error; no secrets in VCS.

### Epic 0.1 — Graph of Truth data model
Goal: the single source of truth — graph + time-bounded mappings + append-only facts — exposed only through repository **ports**.

**Story 0.1.1** — As Admin, I want entities (Program, Project, Pod, Developer, Task) modeled as a graph so that any level can roll up/drill down.
- Tasks: define pure domain entities/value objects (frozen dataclasses) in `core/domain`; define `GraphRepository` port in `core/ports`; implement AGE-backed adapter in `infra/persistence`; typed edges (`CONTAINS`, `ASSIGNED_TO`, `DEPENDS_ON`).
- Acceptance: nodes + typed edges persisted; drill path Program → Project → Pod → Developer → Task queryable in one call; domain layer has zero external imports (enforced by import-linter).

**Story 0.1.2** — As Admin, I want time-bounded `dev ↔ pod ↔ project` mappings so that re-orgs and matrixed devs don't break history.
- Tasks: mapping as first-class edges/rows with `valid_from`/`valid_to`; query `active mapping on date X`; support a dev in multiple pods and a pod in multiple projects.
- Acceptance: querying membership for a past date returns the historically correct mapping; overlapping/matrixed memberships supported; closing a mapping never deletes history.

**Story 0.1.3** — As Admin, I want an append-only event/fact log so that every status is traceable to source facts.
- Tasks: immutable `fact`/`event` table (source, entity ref, payload, observed_at, ingested_at, correlation_id); TimescaleDB hypertable for time-series facts; no UPDATE/DELETE path.
- Acceptance: facts are insert-only; a status can be traced back to its source facts; Timescale retention/compaction policy configured.

**Story 0.1.4** — As a Developer, I want schema migrations so that environments are reproducible.
- Tasks: Alembic migrations (incl. AGE/Timescale/pgvector setup).
- Acceptance: `make migrate` prepares a clean DB for graph writes.

### Epic 0.2 — Identity, roles, and access control (seam only — SSO deferred)
Goal: per [Architecture.md](Architecture.md), real SSO is deferred. Phase 0 builds the *authorization seam* so it can be layered in later without core changes.

**Story 0.2.1** — As Admin, I want a principal/role model and an auth port so that views can be scoped per persona later.
- Tasks: `Role` enum (Dev/PO/SM/Mgr/Exec/Admin); `Principal` value object; `AuthProvider`/`CurrentPrincipal` port; dev-mode stub adapter that injects a configurable principal.
- Acceptance: requests carry a principal; role available to use cases; swapping the stub for a real SSO adapter requires no core change.

**Story 0.2.2** — As Admin, I want a field-/scope-level authorization policy point so that exec-only data (e.g. budget, raw DMs) is gated by design.
- Tasks: central authorization policy in `application` (capability/scope checks, not scattered `if role ==`); mark sensitive fields (budget, raw DM content); default-deny.
- Acceptance: a Dev principal is denied exec-only fields; an Exec sees aggregates, not raw DM content; policy decisions are unit-tested with fakes.

### Epic 0.3 — Integration framework (ports & adapters) and secrets
Goal: prove the provider-abstraction pattern from [Architecture.md](Architecture.md) §5 — one port per capability, vendors behind adapters, selected by config, validated by a shared contract test suite.

**Story 0.3.1** — As Admin, I want the capability ports defined so that all connectors share one pattern.
- Tasks: define `ChatProvider`, `IssueTracker`, `VcsProvider`, `CiProvider`, `CalendarProvider`, `LlmProvider` ports in `core/ports` (interfaces + domain DTOs only, no SDKs).
- Acceptance: ports compile under `mypy --strict`; no vendor name appears in any core symbol.

**Story 0.3.2** — As Admin, I want a shared contract test suite per port so that every adapter is interchangeable.
- Tasks: parametrized contract test per port that runs against any adapter (real + fake); in-memory fakes (`FakeChatProvider`, etc.).
- Acceptance: contract suite runs against the fake for every port and is green; suite is reusable for real adapters added in Phase 1.

**Story 0.3.3** — As Admin, I want one capability proven end-to-end through a real adapter so that the seam is de-risked.
- Tasks: implement the chosen chat adapter (Slack first) with anti-corruption mapping to domain `InboundMessage`/`OutboundMessage`; rate-limit/retry via Redis; webhook intake endpoint.
- Acceptance: the real adapter passes the shared `ChatProvider` contract suite (recorded HTTP via `respx`/VCR); a DM round-trip works against a sandbox.

**Story 0.3.4** — As Admin, I want the config-driven composition root so that providers are swapped by config, not code.
- Tasks: `infra/registry.py` mapping config values → adapter builders (constructor injection, no globals); FastAPI dependency wiring.
- Acceptance: changing `chat_provider` in config swaps the adapter with zero core changes; import-linter confirms `infra → ports` only.

**Story 0.3.5** — As Admin, I want encrypted credential storage and per-tenant/per-connector config so that integrations are secure and isolated.
- Tasks: secret storage abstraction (env/Vault-style provider behind a port); per-connector credential records keyed by `tenant_id`; no plaintext secrets at rest in app DB.
- Acceptance: credentials are encrypted/never logged; structured logging redacts secrets and DM contents.

### Epic 0.4 — Core service skeleton, agents/workflows, and CI/CD
Goal: a runnable backend + frontend shell, the durable workflow engine wired, and a repeatable delivery pipeline with full observability.

**Story 0.4.1** — As Admin, I want the FastAPI app + health/readiness + OpenAPI so that features have a home and a typed contract.
- Tasks: app factory, routers package, DTO conventions (Pydantic at the edge), `/health` + `/ready`; emit OpenAPI for the typed frontend client.
- Acceptance: app boots; OpenAPI served; example router reads the graph through a repository port.

**Story 0.4.2** — As Admin, I want Temporal wired with a trivial scheduled workflow so that long-running check-ins/nudges have a runtime.
- Tasks: worker process; `infra/workflows` with one scheduled "heartbeat" workflow + idempotent activity; Temporal client wiring.
- Acceptance: the scheduled workflow runs on cadence and is visible in Temporal UI; activity is idempotent and retry-safe.

**Story 0.4.3** — As Admin, I want a minimal LangGraph agent skeleton calling an LLM through `LlmProvider`/LiteLLM with Langfuse tracing so that agents have a home.
- Tasks: one trivial LangGraph node that calls the LLM port; LiteLLM gateway config; Langfuse trace on every LLM call.
- Acceptance: invoking the node produces a Langfuse trace (prompt, tokens, cost, latency); LLM access is only via the port.

**Story 0.4.4** — As Admin, I want the React + TS shell with a typed API client and base UI so that frontend features have a home.
- Tasks: Vite + TS (strict) + shadcn/ui + Tailwind; generate typed client from OpenAPI; TanStack Query provider; an app shell page that reads `/health` and a sample graph endpoint.
- Acceptance: `npm run dev` serves the shell; typed client calls the backend; ESLint + Prettier clean.

**Story 0.4.5** — As Admin, I want observability + structured logging baseline so that the system is debuggable from day one.
- Tasks: `structlog` with correlation/trace IDs; OpenTelemetry spans (API → application → adapters); Grafana/Prometheus dashboards for service + DB; PII/DM-content redaction.
- Acceptance: a request produces a correlated trace across layers; dashboards show basic golden signals; no PII/DM content in logs.

**Story 0.4.6** — As Admin, I want CI/CD and environments so that delivery is repeatable.
- Tasks: CI pipeline running `ruff` (lint + format check), `mypy --strict`, `import-linter`, unit + contract + integration tests (Testcontainers Postgres), coverage gate on `core/` (≥85%); build/push images; deploy to a `dev` environment; pre-commit hooks; Conventional Commits.
- Acceptance: PRs are blocked on a red pipeline; a merge to main deploys to `dev`; coverage gate enforced.

---

### Phase 0 Definition of Done (cross-cutting)
Applies to every story above:
- Hexagonal dependency rule holds (`api → application → domain`, `infra → ports`), verified by `import-linter` in CI.
- Domain/application covered by fast pure unit tests (no I/O); adapters covered by the shared contract suite; integration paths use Testcontainers + recorded HTTP.
- `ruff`, `ruff format`, and `mypy --strict` pass; `Any` only at isolated SDK seams.
- All external mutations are idempotent, logged, and reversible (audit trail); no secrets or DM contents in logs.
- Conventional Commit + small PR referencing the epic/story.

### Phase 0 suggested sequencing
1. Epic 0.0 (repo, compose, config) → 2. Epic 0.1 (graph + facts + migrations) in parallel with Epic 0.3.1–0.3.2 (ports + contract suite) → 3. Epic 0.3.3–0.3.5 (first real adapter + secrets) and Epic 0.4.1–0.4.3 (API + Temporal + agent skeleton) → 4. Epic 0.2 (auth seam) and Epic 0.4.4–0.4.6 (frontend shell + observability + CI/CD).

### Phase 0 exit criteria (gate into Phase 1)
- Graph of Truth queryable end-to-end with time-bounded mappings and append-only facts.
- Ports + shared contract suite exist for all capabilities; the first real adapter (chat) passes its contract suite end-to-end.
- Temporal runs a scheduled workflow; a LangGraph node calls the LLM with Langfuse tracing.
- Green CI (lint, types, import-linter, tests, coverage) and an automated deploy to `dev`; one-command local bring-up works.

---

## Phase 1 — Sense (prove the core loop)
Goal: proactively collect daily status, roll it up, and show basic views.

### Epic 1.1 — Read-only integrations
- As Admin, I want read-only Jira sync (projects, sprints, issues, transitions) so that tasks populate the graph.
- As Admin, I want read-only Git sync (repos, branches, commits, PRs) so that activity signals exist.
- As Admin, I want Teams/Slack bot connectivity (DM send/receive) so that the check-in agent can talk to devs.
- As Admin, I want calendar/PTO read so that check-ins respect availability and timezone.

### Epic 1.2 — Daily Status Collector agent
- As a Dev, I want a proactive daily DM at my preferred time so that I give status without a meeting.
- As a Dev, I want context pre-filled (my in-progress issues) so that I confirm/correct instead of typing from scratch.
- As a Dev, I want free-text replies parsed into structured signals (progress, blockers, ETA, mood) so that one sentence is enough.
- As an SM, I want non-responders nudged once then marked "stale/unknown" so that silence is never assumed green.

### Epic 1.3 — Roll-up engine v1
- As an SM, I want pod status derived from dev statuses using weighted rules (blockers, critical-path) so that it reflects reality not averages.
- As a PO/Mgr, I want project status derived from pods, and program from projects, so that every level has a current status.
- As any persona, I want to drill from a level down to the source dev/task so that status is explainable.

### Epic 1.4 — Persona views v1
- As a Dev, I want a lightweight view of my tasks, blockers, and deadlines so that I know my focus.
- As an SM, I want a blocker board and check-in completeness view so that I run the team daily.
- As a PO, I want epic/feature progress so that I track scope.
- As a Mgr/Exec, I want a hierarchy tree and portfolio heatmap (RAG) so that I see health at a glance.

---

## Phase 2 — Reconcile
Goal: make status honest by cross-checking words against system signals, and close the loop back into Jira.

### Epic 2.1 — Hard-signal ingestion
- As the system, I want PR review state, commit cadence, and CI/CD results ingested so that "actual progress" is measurable.

### Epic 2.2 — Reconciliation and confidence scoring
- As an SM, I want stated status compared to Jira/Git/CI facts so that mismatches surface.
- As any persona, I want every status to carry a confidence score (human-confirmed vs inferred) so that I know how much to trust green.

### Epic 2.3 — Drift detection
- As a Mgr/Exec, I want "watermelon" detection (green outside, red inside) so that hidden risk is exposed early.
- As an SM, I want drift alerts (e.g. "said done, no PR merged") so that I follow up.

### Epic 2.4 — Jira write-back
- As a Dev, I want my check-in answers to update Jira (transitions, comments, remaining estimate) with my permission so that I avoid double entry.
- As Admin, I want all agent writes logged and reversible so that automation is auditable.

---

## Phase 3 — Predict
Goal: move from reporting the present to forecasting risk and automating escalation.

### Epic 3.1 — Risk and blocker engine
- As an SM, I want stale tasks, repeated blockers, and scope creep detected so that I act before they spread.

### Epic 3.2 — Milestone slip prediction
- As a PO/Mgr, I want sprint/release miss probability forecast from velocity, scope, and blocker trends so that I replan early.

### Epic 3.3 — Dependency-stall detection
- As a Mgr, I want cross-team dependency stalls flagged (Team A waiting on Team B) so that I unblock before it's a fire.

### Epic 3.4 — Smart escalation policies
- As an SM/Mgr, I want blocker-aging rules that auto-escalate Dev to SM to Mgr so that nothing rots silently.

### Epic 3.5 — Advanced visualization
- As any persona, I want flow/cumulative-flow diagrams, dependency graphs, blocker timelines, and trend sparklines so that I see momentum and bottlenecks.

---

## Phase 4 — Coach
Goal: people-health, optimization, and natural-language access across the whole graph.

### Epic 4.1 — Sentiment and burnout signals
- As a Mgr, I want privacy-respecting aggregate sentiment/overload signals (after-hours patterns, declining tone) so that I support people before burnout.

### Epic 4.2 — Load-balancing suggestions
- As a Mgr, I want reassignment suggestions when one dev is overloaded and another has slack so that work is balanced.

### Epic 4.3 — Auto standup and retro generation
- As an SM, I want auto-generated standup digests and data-backed retro inputs so that meetings shrink and improve.
- As a PO/Exec, I want on-demand stakeholder updates ("5-line update on Project B") so that reporting is instant and sourced.

### Epic 4.4 — Ask-anything natural-language query
- As any persona, I want to ask "Which projects are at risk because of the payments API?" so that I query the whole graph in plain language.

### Epic 4.5 — Knowledge and decision memory
- As a Dev/SM, I want recurring blockers matched to past resolutions, and new joiners given an auto map of their pod/projects, so that institutional knowledge persists.

---

## Notes
- Each phase is independently shippable and demoable; Phase 1 alone proves the core agentic loop.
- Suggested tech direction (graph store + time-series, durable workflow engine, LLM tool-calling agents, role-aware React frontend) is in [OpenProgramConcept.md](OpenProgramConcept.md) section 8.
- Open decision before build: confirm primary chat platform (Teams vs Slack), Jira Cloud vs Server, and whether to start single-tenant or multi-tenant.
