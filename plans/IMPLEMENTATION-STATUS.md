# Implementation Status — plans/ execution handoff

Working branch: **`feat/openprogram-hardening`** (off `master`). All work below is **uncommitted** in the working tree (no commits made per instruction). Recommend a checkpoint commit before continuing.

## Verified baseline (as of this handoff)
Run from repo root; all currently GREEN:
- `uv run ruff check .` → clean
- `uv run ruff format --check .` → clean **except** the pre-existing, out-of-scope `backend/tests/contract/test_gitlab_adapter.py` (unformatted at HEAD; leave it)
- `uv run lint-imports` → Contracts: 5 kept, 0 broken
- `PYTHONPATH=backend uv run mypy` → no issues (153 files)
- `PYTHONPATH=backend uv run pytest backend/tests/unit backend/tests/contract --no-cov` → **395 passed**
- `PYTHONPATH=backend uv run pytest backend/tests/bdd` → **25 failed / 14 passed / 10 skipped** — this is the PRE-EXISTING baseline (mock-slack "no bot message found"; environmental — needs the running simulator/LLM, verified identical at HEAD). Do not treat these as regressions.
- Frontend: `cd frontend && npm run typecheck` → clean. `openapi.json` + `generated.ts` regenerated and in sync.

Alembic migrations added: `0019_inbound_chat_events`, `0020_checkin_local_date`, `0021_identity_links`, `0022_writeback_audit`. **Next free number: `0023`.**

## DONE + verified
- **Plan 01 — Reliability** (`plans/01-reliability.md`): fast-ack webhook + Slack signature-verification fix + `event_id`/retry dedup; durable async coalescing on DBOS (30s reset-on-message debounce) with `inbound_chat_events` buffer + drain step + sweeper safety-net; `ReplyIngestionService` (fixes lost-reply-on-LLM-failure); bounded-concurrent fanout. Mirrored in Temporal.
- **Plan 02 — Correctness** (`plans/02-correctness.md`): C2 JSON-mode + fence-tolerant parse + **safe fallbacks** (garbled reply never finalizes as healthy); C3 honored send-once idempotency (`send_once.py`); C1 timezone/DST coherence (`local_date`/`resolve_timezone` domain helpers, developer-local `checkin_date` column + roster rewrite with UTC fallback, DTO timezone validator, DST-safe scheduling); C4 identity mapping (`IdentityLink`, `IdentityLinkRepository`, `build_context` resolves Jira accountId; config API `GET/PUT /config/members/{id}/identity-link`; field is `chat_user_id` — NOT `slack_*`, per the provider-neutrality guard).
- **Plan 03 Feature A — Jira write-back** (`plans/03-new-features.md` §A): 3 default-deny gates (tenant `jira_writeback_enabled` admin flag via `GET/PUT /config/tenant/writeback` → `Capability.WRITE_ISSUE_TRACKER` → per-dev `write_back_consent` {always_ask|auto_apply|never}, default always_ask); real Jira `transition`/`add_comment`; idempotent audited `WriteBackService` (+`revert()`); `writeback_audit`+`writeback_config` tables. Architecture guard tightened: only `writeback_service.py` may call the write path.
- **Plan 03 Feature B — Drift/watermelon detection** (`plans/03-new-features.md` §B): extends `risk_service`/`risk.py`; findings `said_done_no_pr`/`claimed_progress_no_activity`/`green_over_red`; scheduled `drift_scan` via the shared sync-workflow path in both engines + `drift_scan_cron`; contradicted statuses downgraded out of `confirmed` (never green); exposed on persona risk endpoints (`drift: [...]`).

## DONE + verified (continuation — this window)
- **Plan 04 §4f security boot-guard** (`config/settings.py`): `Settings` now fails startup when `environment != "local"` and either `auth_provider="dev"` (unauthenticated admin) or `secret_key` is the public default committed for local bring-up. Enforced in the cross-field `model_validator` (via `_guard_shared_deployment`), so it fires at `get_settings()`. Tested in `test_settings.py`. Commit `b706893`.
- **Plan 04 §4e delivery hygiene (partial)** (`.github/workflows/ci.yml`): push trigger + deploy gate moved from `main` → `master` (direct pushes were skipping CI); removed the no-op `deploy-dev` echo job. Commit `4828849`. **Still pending in §4e:** coverage-gate extension to `infra`/`api` — current core+infra+api coverage is ~75% on unit+contract (below the 85% gate); needs new tests or a ratcheted threshold measured *with* integration tests, so deferred as a separate effort.
- **Plan 03 Feature C — Trends/sparklines** (§C): `RollupRepository.node_status_history` (postgres + in-memory + contract fake); `PersonaViewService.node_trend` → ordinal-scored daily points; `NodeTrendResponse` DTO; `GET /persona/{level}/{id}/trend` (aggregate-gated, `window_days` 1–365, 422 for kinds without rollups). Frontend `Sparkline` (ECharts, RAG via CSS tokens) + Manager-only "Delivery Momentum" panel (improving/steady/sliding) so Manager ≠ Exec. Commits `470452a`, `94b5b8e`. **Note:** regenerating the client also normalized a pre-existing `openapi.json` drift from the pinned prettier 3.8.4 in the lockfile (HEAD was unformatted) — the CI api-contract drift gate would have failed at HEAD regardless.
- **Plan 03 Feature D — Click-to-drill** (§D): `personaDrillPath` helper; wired ECharts cell clicks on `HeatmapChart`, ReactFlow `onNodeClick` on `HierarchyFlow`, and clickable heatmap rows → detail routes (project/pod/workstream; program/developer/task have no page and are non-interactive). Commit `27a5740`.
- **Plan 03 Feature B drift-list rendering** (deferred sub-item): `RisksPage.tsx` now shows a Drift-findings KPI + a Drift/Watermelon table. Commit `7b098cc`.

## REMAINING
- **Plan 03 Feature E — Escalation ladder** (§E): `nudge_number` is hardcoded to 1; add N-step escalation + policy config; consult `AvailabilityService` before nudging. **BLOCKED on a design decision:** escalation to a human (dev → SM → manager) needs the SM/manager for a developer, but `DirectoryUser` has **no role field** and there is **no graph relationship** modeling pod→SM/manager. Resolve first: (a) add pod-level escalation contacts to config + Admin UI, (b) role-broadcast to tenant SM/mgr principals, or (c) ship dev-only repeated escalation now and defer human escalation. Also a two-engine durable-loop rewrite (`dbos_nudge_workflow` + Temporal mirror) that can't be end-to-end verified without the full stack.
- **Plan 03 Feature F — Scheduled narrative briefs** (§F): daily pod / weekly project / exec brief on a schedule via `ask_service`+`portfolio_feed_service`+LLM. Large: new workflow + cadence/recipient config, mirrored in both engines; recipient resolution has the same "who receives it" question as E.
- **Plan 04 — System helpfulness / ops** (`plans/04-system-helpfulness.md`): §4f boot-guard **DONE**; §4e CI branch/deploy **DONE** (coverage-gate extension still pending, see above). Remaining: **engine consolidation deferred by user decision — keep both engines for now** (so every workflow change must still be mirrored in DBOS + Temporal); slim docker-compose (opt-in Langfuse/Temporal profiles); remove dead infra (AGE graph, pgvector/VectorStore, CiProvider, StatusAgentNode, heartbeat); dead-letter/alerting for stuck check-ins; plus the UX polish cross-refs (contextual check-in prefill, "got it" ack, low-confidence transparency, write-back adoption metric).

### Deferred sub-items inside DONE features (pick up when convenient)
- Plan 01: Temporal `continue_as_new` for very long coalesce conversations; dedicated burst/retry BDD scenarios (covered by unit tests for now).
- Plan 02 C4: frontend Admin UI for identity links; directory email auto-match to pre-populate links.
- Plan 03 A: interactive DM consent loop (propose diff → await yes/no; currently records a `proposed` audit row); consent-setting API + Admin UI; undo HTTP endpoint (`WriteBackService.revert()` exists + tested, no route yet).
- Plan 03 B: frontend `RisksPage.tsx` rendering of the new `drift` list — **DONE** (commit `7b098cc`).

## Execution notes for the next window
- **Repo protocol (Lore):** before editing files run `lore constraints/rejected/directives <path> --json` (see `.agents/skills/lore-protocol/SKILL.md`). Commits go via `lore commit` (staged files + JSON intent/trailers).
- **Architecture:** hexagonal, import-linter enforced (`core` never imports `infra`/`api`/`config.settings`; provider values thread through constructors). A guard test bans the literal `slack` in `core`/`api`.
- **Sub-agent caveat:** background agents in this environment repeatedly hit a 600s stream watchdog during long file-reading phases (and one was lost to a session restart). **Synchronous agent runs (`run_in_background:false`) and inline edits were reliable.** For investigation-heavy tasks, pre-load the brief with exact code/line refs to minimize reading, or run synchronously.
- **Verify after every sub-step** against the 395 unit+contract baseline; regenerate OpenAPI + client whenever API routes/DTOs change (CI has a drift gate).
