# Plan 04 — Making the System More Helpful

This plan turns "impressive engineering demo" into "a tool a program manager relies on daily." Three of its four themes are **delivered by Plans 01–03** and are cross-referenced (not duplicated) here; the fourth — **operational fragility** — is unique to this plan and specified in full. A **sequencing** section at the end ties all four plans together.

---

## Theme 1 — Make check-ins actually contextual

*Goal:* the DM says *"You moved `OPDAY1-1` to In Progress yesterday — done or still going?"* instead of a generic prompt. Confirm-not-type is the entire low-friction premise.

- **Delivered by:** Plan 02 **C4** (Slack↔Jira identity mapping) + `build_context` using the resolved Jira account id. This is the single biggest lever and is currently blocked (UAT-D1-003).
- **Add here (UX polish once C4 lands):**
  - Pre-fill the DM with the developer's active issues and yesterday's carried-forward blockers as tappable confirm/correct items (the concept's "mostly confirm, occasionally correct").
  - Show "unmapped members" to admins so degraded check-ins are visible and fixable.
- **Acceptance:** for a mapped developer, ≥80% of check-in DMs reference at least one real active issue.

---

## Theme 2 — Make the reply path trustworthy

*Goal:* the bot never loses a reply, never double-DMs, and never silently records a blocked developer as green.

- **Delivered by:** Plan 01 (fast-ack, dedup, coalescing, sweeper safety-net, no lost replies) + Plan 02 **C2** (no silent-healthy fallback) and **C3** (no duplicate DMs).
- **Add here (user-facing trust):**
  - A lightweight *"Got it 👍"* acknowledgement once a reply is accepted, so the developer knows the bot received them (the async path from Plan 01 makes this the right moment to ack).
  - A transparency line when parsing was low-confidence: *"I recorded this as in-progress with a blocker on the API review — reply 'fix' if that's wrong."* This makes Plan 02 C2's confidence signal visible and self-correcting.
- **Acceptance:** every accepted reply produces a visible ack; low-confidence parses are surfaced to the developer for correction.

---

## Theme 3 — Close the write loop

*Goal:* the developer's check-in keeps Jira accurate, removing the double-entry the product promises to eliminate. Read-only means the dev still updates Jira by hand.

- **Delivered by:** Plan 03 **Feature A** (Jira write-back with system-level admin control + per-developer consent, audited and reversible).
- **Add here:** measure the payoff — track "Jira updates applied via check-in" as an adoption metric on the admin/exec surface, so the time-saved is visible to the people deciding whether to keep using it.
- **Acceptance:** with write-back enabled, a measurable share of Jira transitions/comments originate from consented check-ins.

---

## Theme 4 — Reduce operational fragility (unique to this plan)

The UAT log shows a scheduled fanout that got stuck in DBOS `ENQUEUED` and needed **manual version-retagging** to recover — the kind of incident that erodes trust in an "always-on" agent. The stack is also far heavier than the default path needs.

### 4a. Consolidate to one workflow engine

Two full engines are maintained in parallel — DBOS (`infra/adapters/workflows/dbos.py`, 693 LOC) and Temporal (`temporal.py`, 717 LOC) — but only DBOS runs by default (`settings.workflow_provider="dbos"`). Every workflow change must be mirrored in both, and Temporal + `temporal-ui` run in compose unused.

- **Decision to make:** keep DBOS as the single engine (simplest, already default, Postgres-only) **or** keep Temporal (richer primitives — its `signal_with_start` is the cleanest fit for Plan 01's debounce). Pick one.
- **Action:** remove the unused engine adapter and its compose services, or move it behind an explicitly-labeled, tested optional profile. Removing one engine roughly halves the orchestration surface that must stay in sync — and eliminates the app-version-mismatch class of incident seen in UAT.

### 4b. Slim the runtime; remove hard coupling

- `backend` currently `depends_on … langfuse-web: service_healthy`, so **nothing boots locally without the entire 5-service Langfuse + ClickHouse + MinIO stack**, even though `memory` mode uses a `NoopTraceSink`. Remove the hard health-dependency; make Langfuse (and Temporal, Prometheus/Grafana) **opt-in compose profiles**.
- Default local bring-up should need only Postgres + Redis + LiteLLM + the app.

### 4c. Remove dead infrastructure (maintenance, latency, and attack-surface reduction)

Each of these is built, maintained, and **unused** — carrying cost with no return (see the assessment):

| Component | Why it's dead | Action | Status |
|---|---|---|---|
| **Apache AGE graph** | Written on every node/edge mutation but **never read** (no `cypher(` in any read path; relational recursive CTEs are the real source of truth). Doubles write latency + is a Cypher-injection surface + required for readiness | Remove the AGE sync + extension requirement; drop from `readiness.py` | Removed in `e45c4d9`, **restored** in migration `0026` — see note below |
| **pgvector / `VectorStore`** | Instantiated + implemented, called by no use case | Remove until a semantic-search feature needs it | Removed in `e45c4d9`, **restored** in migration `0026` |
| **`CiProvider` port** | No adapter, no references | Remove | Removed in `e45c4d9`, **restored** |
| **`StatusAgentNode` LangGraph** | Only caller is a demo smoke script | Remove | Removed in `e45c4d9`, **restored** |
| **`heartbeat` workflow** | No-op liveness beacon | Remove or justify | KEPT — justified as the liveness beacon |

> **Superseded for four of five rows.** All four removed components were restored on request. The AGE
> graph came back as a **parameterized** implementation: values are bound via the `cypher()` third
> argument as an `agtype` document (`_age_params`), so the hand-rolled `_cypher_string` escaping — and
> the Cypher-injection surface this section called out — is *not* back. Relationship labels still cannot
> be bound in Cypher, so they come from a closed `EdgeKind` → literal map.
>
> Two consequences of the restore that this plan's rationale did not anticipate: the write-latency cost
> of the AGE mirror returns, and AGE becomes a hard runtime extension dependency that rules out
> RDS/Aurora independently of TimescaleDB (see `docs/ops/infrastructure-procurement.md` DP-1). The
> "never read" observation still holds — nothing reads the AGE mirror, and it is not backfilled, so it
> reflects only mutations since restore.

### 4d. Dead-letter + alerting for stuck check-ins/replies

The current failure mode ("turn recorded but never finalized," recovered only hours later by the timeout workflow) has no operator visibility.

- Build on Plan 01's `inbound_chat_events` sweeper: emit a **metric/gauge** for events `processed_at IS NULL` beyond grace, and workflows in `ENQUEUED`/failed state beyond a threshold.
- Add a **dead-letter** record for replies/check-ins whose durable retries are exhausted, surfaced on an admin "operations" view, with a one-click re-arm.
- Extend `/ready` to report workflow-scheduler health (not just DB reachability) so a stuck scheduler is detectable.

### 4e. Make delivery itself dependable

- **`deploy-dev` is a no-op `echo`** in CI — either implement a real deploy or remove the misleading step.
- CI triggers on push to **`main`** but the branch is **`master`** — direct pushes skip CI. Fix the branch name.
- The 85% coverage gate measures **`backend/core` only** (`pyproject.toml:58`); the highest-risk code (SQL, auth middleware, webhook handling, the new inbound-event path) is excluded. Extend coverage to `infra`/`api`.
- Commit and reconcile the working docs (UAT results live only in an **untracked** `uat.md`; the GitLab-provider status contradicts itself across docs vs. compose default vs. actual config; `slack.md` still says "PulseOps").

### 4f. Prerequisite security hardening (gates the "always-on/shared" story)

These must land before the system runs anywhere shared (detailed in the assessment; folded into Plan 01 for the webhook):

- Webhook signature bypass (`registry.py:273` + `catalog.py:113`) — **fixed in Plan 01**.
- Default `auth_provider="dev"` = unauthenticated admin, and the committed default Fernet `SECRET_KEY` — add a **boot-time guard** that fails startup when `environment != "local"` and either the dev auth provider or the default secret key is in use.

---

## Sequencing across all four plans

```
Step 0  Security prerequisites (4f): boot guard for dev-auth/default-key.
Step 1  Plan 01 Reliability — fast-ack + coalescing + dedup + sweeper (+ webhook signature fix). 
Step 2  Plan 02 Correctness — C2 (structured output) + C3 (idempotency) harden Plan 01's retries;
        then C1 (timezone) migration; then C4 (identity mapping).
Step 3  Plan 03 Feature A — Jira write-back (needs C4). Themes 1–3 of this plan are now delivered.
Step 4  Plan 04 operational: consolidate engine (4a), slim stack (4b), remove dead infra (4c),
        dead-letter/alerting (4d), delivery hygiene (4e).
Step 5  Plan 03 B–F — drift, trends, drill, escalation, briefs — as capacity allows.
```

Operational items (4a–4e) are largely independent and can be interleaved earlier; 4c (remove dead infra) is safest done **after** Step 1 stabilizes, since it touches persistence and readiness.

## Overall acceptance

- Local bring-up needs only Postgres + Redis + LiteLLM + app.
- One workflow engine; no manual version-retagging to recover a scheduled run.
- Stuck replies/check-ins are visible to operators and re-armable, never silently lost.
- The system is safe to run in a shared environment (no unauthenticated admin, no shared default encryption key).
- A program manager sees contextual check-ins, a trustworthy reply loop, and Jira staying accurate without double entry.
