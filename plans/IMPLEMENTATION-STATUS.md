# Implementation Status — plans/ execution handoff

Working branch: **`feat/openprogram-hardening`** (off `master`). Work is committed via `lore commit` (see Execution notes).

## Verified baseline (current)
Run from repo root; all GREEN:
- `uv run ruff check backend` → clean
- `uv run ruff format --check backend` → clean **except** the pre-existing, out-of-scope `backend/tests/contract/test_gitlab_adapter.py` (unformatted at HEAD; leave it)
- `uv run lint-imports` → Contracts: 5 kept, 0 broken
- `PYTHONPATH=backend uv run mypy` → no issues (157 files)
- `PYTHONPATH=backend uv run pytest backend/tests/unit backend/tests/contract --no-cov` → **432 passed**
- Frontend: `npm run typecheck` / `lint` / `format:check` / `build` → clean. `openapi.json` + `generated.ts` regenerated and in sync (regenerate with the LOCAL prettier: `./node_modules/.bin/prettier`, NOT a global `npx prettier` — versions differ and cause spurious drift).
- `backend/tests/bdd` remains the PRE-EXISTING environmental baseline (needs the running simulator/LLM; not a regression).

Alembic migrations through `0025_dead_letters`. **Next free number: `0026`.**

## DONE + verified
- **Plan 01 — Reliability**: fast-ack webhook + Slack signature-verification fix + `event_id`/retry dedup; durable async coalescing (30s reset-on-message debounce) with `inbound_chat_events` buffer + drain + sweeper safety-net; `ReplyIngestionService` (no lost-reply-on-LLM-failure); bounded-concurrent fanout. Mirrored in Temporal.
- **Plan 02 — Correctness**: C2 JSON-mode + fence-tolerant parse + safe fallbacks (garbled reply never finalizes healthy); C3 send-once idempotency; C1 timezone/DST coherence (developer-local `checkin_date`); C4 identity mapping (`IdentityLink`, `build_context` resolves Jira accountId; `GET/PUT /config/members/{id}/identity-link`; field is `chat_user_id`, provider-neutral).
- **Plan 03 A — Jira write-back**: 3 default-deny gates (tenant `jira_writeback_enabled` → `Capability.WRITE_ISSUE_TRACKER` → per-dev `write_back_consent` {always_ask|auto_apply|never}); real Jira `transition`/`add_comment`; idempotent audited `WriteBackService` (+`revert()`, tested, no route yet); `writeback_audit`+`writeback_config` tables. Only `writeback_service.py` may call the write path.
- **Plan 03 B — Drift/watermelon**: `risk_service`/`risk.py` findings `said_done_no_pr`/`claimed_progress_no_activity`/`green_over_red`; scheduled `drift_scan` in both engines; contradicted statuses kept out of green; on persona risk endpoints; **frontend Drift KPI + table on `RisksPage.tsx`** (commit `7b098cc`).
- **Plan 03 C — Trends/sparklines** (`470452a`, `94b5b8e`): `RollupRepository.node_status_history`; `PersonaViewService.node_trend`; `GET /persona/{level}/{id}/trend`; frontend `Sparkline` + Manager-only "Delivery Momentum" panel (Manager ≠ Exec).
- **Plan 03 D — Click-to-drill** (`27a5740`): `personaDrillPath`; ECharts cell + ReactFlow node + list-row clicks → detail routes.
- **Plan 03 E — Escalation ladder** (`9c31f4c`, `36994c0`, `4c5bab0`, `5bbc83c`): pod-level escalation contacts (`GET/PUT /config/pods/{id}/escalation-contacts`); `EscalationPolicy`/settings ladder; `send_nudge(nudge_number, target, recipient)` with privacy-safe SM/manager notice; N-step dev→SM→manager loop in both engines with `AvailabilityService` PTO suppression + pod-contact resolution.
- **Plan 03 F — Scheduled narrative briefs** (`d9584c8`): `NarrativeBrief`/`BriefKind`; `NarrativeBriefRepository` (migration `0023`); `NarrativeBriefService.generate` (descriptive, sourced, privacy-safe, LLM + fallback); `brief_generation` scheduled in both engines; `GET /persona/briefs`. Chat delivery stubbed off.
- **Frontend surfacing for E + F** (`54287ee`): per-pod Escalation dialog on AdminConfigPage; Manager+Exec Narrative Briefs panel.
- **Plan 04 §4f — security boot-guard** (`b706893`): startup fails when `environment != local` and dev-auth or the default `secret_key` is in use.
- **Plan 04 §4e — CI branch/deploy** (`4828849`): CI runs on `master`; removed the no-op `deploy-dev`. (Coverage-gate extension still deferred — see below.)
- **Plan 04 §4b — slim docker-compose** (`3ce4cdb`): langfuse/temporal/observability are opt-in profiles; default `up` = postgres+redis+litellm+mock-llm+backend+worker.
- **Plan 04 §4c — remove dead infra** (`e45c4d9`): removed AGE graph sync + Cypher-injection surface, pgvector/VectorStore, CiProvider/BuildResult, StatusAgentNode; readiness needs only `timescaledb`; migration `0024`. heartbeat KEPT (justified liveness beacon).
- **Plan 04 §4d — dead-letter + alerting** (`57c0c29`): `DeadLetter` domain + repository (migration `0025`, identifiers only); sweeper dead-letters exhausted bursts; admin ops `GET/POST /admin/ops/dead-letters[/{id}/rearm]`; workflow-backlog gauge + `/ready` `workflow_backlog` dependency.

## REMAINING — to do now (this batch)
Being implemented feature-by-feature via sub-agents; each committed + this doc updated on completion.
- **UX Theme 2 — reply-path trust — DONE** (commit `998a097`): "Got it 👍" ack DM on reply finalization (single chokepoint, idempotent, not in conversation history); low-confidence acks (`CheckInSignals.parser_confident` False) add a recorded-summary + "reply 'fix'" hint. `checkin_ack_enabled` setting.
- **UX Theme 1 — contextual check-in prefill — DONE (satisfied by existing code + §C4 + Task #15):** `build_context` already surfaces active prioritized issues + latest-status/carried-forward blockers, and the compose-DM prompt references a specific pending/blocked/stale issue + carried-forward blocker; carried-forward blockers are threaded in the reply path (`_signals_with_carried_blockers`); confirm/correct affordances exist (`/me/status/confirm|correct` + dashboard buttons); "unmapped members" admin visibility shipped in `f535e48`. The literal "tappable" block-kit affordance is Slack-specific and out of scope for the provider-neutral text bot.
- **UX Theme 3 — write-back adoption metric:** track "Jira updates applied via check-in" on the admin/exec surface.
- **Plan 03 A undo + adoption — DONE** (commit `c27a500`): `POST /admin/ops/writeback/{audit_id}/revert` (through `WriteBackService`, idempotent, 404/409/502); adoption count via `GET /persona/writeback-adoption` + `openprogram_writeback_applied` gauge + Manager/Exec "Jira updates via check-in" KPI.
- **Plan 03 A consent loop — REMAINING:** interactive DM consent (propose diff → await yes/no; today records a `proposed` row) + consent-setting API + Admin UI.
- **Plan 02 C4 follow-ons — DONE** (commit `f535e48`): identity-link Admin dialog on AdminConfigPage; `POST /config/members/identity-links/auto-match` (fills only missing fields from directory: external_id→chat_user_id, email→jira_email); `GET /config/members/unmapped` + unmapped-count KPI.
- **Plan 01 follow-ons:** Temporal `continue_as_new` for very long coalesce conversations; dedicated burst/retry BDD scenarios.

## DEFERRED — not in this batch (per user)
- **Plan 04 ops bucket:** §4a engine consolidation (keep both engines by decision); §4e coverage-gate extension to `infra`/`api`; §4e doc reconciliation (`uat.md`/`slack.md`/`checin.md` scratch files, "PulseOps" string, GitLab-provider doc contradiction).

## Verification gaps (environment, not code)
- Durable multi-step execution (escalation ladder, brief scheduling, dead-letter sweeper) in **both engines**, and **migrations 0023/0024/0025 against a live Postgres**, are unverified here — they need the running stack (`OPENPROGRAM_RUN_INTEGRATION=1` + docker) and the integration/BDD suites. Run before shipping.

## Execution notes
- **Lore protocol:** before editing files run `lore constraints/rejected/directives <path> --json` (see `.agents/skills/lore-protocol/SKILL.md`). Commit via `lore commit` (staged files + JSON intent/trailers). `Supersedes`/`Depends-on`/`Related` trailers require 8-char hex lore-ids (use plain body text otherwise).
- **Architecture:** hexagonal, import-linter enforced (`core` never imports `infra`/`api`/`config.settings`; provider values thread through constructors). A guard test bans the literal `slack` in `core`/`api` — keep provider-neutral (use `chat_external_id`, not provider fields).
- **Both engines:** any workflow change must be mirrored in DBOS (`infra/adapters/workflows/dbos.py`) AND Temporal (`temporal.py`); keep payloads JSON-native (ISO strings, no datetime objects).
- **Verify after every sub-step** against the unit+contract baseline; regenerate OpenAPI + client (local prettier) whenever API routes/DTOs change (CI has a drift gate).
- **Sub-agent caveat:** background agents can hit a 600s watchdog on long file-reading; prefer `run_in_background:false` and pre-load briefs with exact file/line refs.
