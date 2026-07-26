# Plan 03 — New Features

The current system **senses and reports**. Its differentiated value — per `OpenProgramConcept.md` — is **reconcile / predict / close-the-loop**, which is exactly what's unbuilt. Features are ordered by value; **Feature A (Jira write-back) is specified in most detail** because it carries an explicit product constraint.

---

## Feature A — Jira write-back, with consent + system-level admin control

**Requirement (from the request):** whether write-back is allowed is a **system-level configuration controlled by an admin**; and every individual write requires the **developer's consent**. Default **off**.

### Current state

- The `IssueTracker` port already **reserves** `transition` and `add_comment` — "for later write-back phases. Phase 1 application code must not call this" (`core/ports/issue_tracker.py:26,29`).
- The Jira adapter raises `ProviderUnavailable("issue tracker adapter is read-only")` for both (`infra/adapters/jira/jira_adapter.py:81,84`).
- Check-in parsing already extracts `issue_updates` (`issue_key`, `claimed_done`, `claimed_state`, `note`) as `IssueClaim`s (`status_parsing.py:334`) — the raw material for a write is already captured.

### Design — three gates, default-deny

A write happens **only if all three** hold:

1. **System gate (admin).** `jira_writeback_enabled` — a tenant-level runtime flag (store in the runtime-config graph so it's multi-tenant-safe, not just a static env). Toggled via an admin config endpoint + Admin UI switch, gated by `MANAGE_CONFIG`. Default `False`.
2. **Capability gate.** New `Capability.WRITE_ISSUE_TRACKER` in `core/application/authorization.py`; admin controls the flag, the developer's principal authorizes writes **to their own issues only**.
3. **Consent gate.** Per-write, explicit developer consent (see flow). Plus a standing per-developer preference: `always_ask` (default) / `auto_apply` / `never`.

### Consent flow (in the check-in conversation)

When a reply yields an `IssueClaim` that implies a Jira change (e.g. `claimed_done=true`, a state change, or an ETA/remaining-estimate change) **and** the system + capability gates are open:

1. The bot proposes the concrete diff in the DM: *"Want me to move `OPDAY1-1` to Done and add your note as a comment? (yes / no)"*.
2. Store the proposed change + a `consent_pending` record correlated to the check-in.
3. On affirmative reply → apply. On negative / no reply → record `declined`/`expired`, never write.
4. If the developer's standing preference is `auto_apply`, skip the prompt but still record consent provenance (`standing_consent`); if `never`, never prompt.

### Implementation

- **Adapter:** implement `JiraIssueTrackerAdapter.transition` and `add_comment` (replace the raises at `:81,84`). Real Jira Cloud:
  - transition: `GET /rest/api/3/issue/{key}/transitions` to resolve the transition id for `to_state`, then `POST .../transitions`.
  - comment: `POST /rest/api/3/issue/{key}/comment`.
  - remaining estimate: `PUT /rest/api/3/issue/{key}` fields.
  - Reuse existing auth/retry/JQL-escaping infra in the adapter.
- **Application service:** `core/application/writeback_service.py::WriteBackService` enforces the three gates, resolves the consented diff, calls the port, and writes an **append-only audit** + is **idempotent** (don't re-apply an already-applied transition; key on `(issue_key, target_state, correlation_id)`). This is the "Phase 2 unlock" the port comment anticipates.
- **Audit + reversibility:** `writeback_audit` table (who, what, when, before→after, correlation_id, source check-in). Store prior state to support an **undo** action. Every write logged and reversible per the architecture's audit rule.
- **Config plumbing:** `settings.py` fallback default `jira_writeback_enabled: bool = False`; runtime override in the config graph; admin endpoints `GET/PUT /config/tenant/writeback`; Admin UI toggle.
- **Contract tests:** the current read-only contract asserts `transition`/`add_comment` **raise** — update to: raise when the system gate is closed (a `WriteDisabled` path), and, with the gate open, assert correct Jira calls via `respx`-recorded transition/comment payloads.

### Acceptance

- With the flag **off**, no code path can write to Jira (contract-tested).
- With the flag **on**, a developer who confirms "yes" sees the Jira issue transitioned + commented; the write is audited, idempotent, and reversible.
- A developer set to `never`, or who declines, never triggers a write.

---

## Feature B — Drift / "watermelon" detection surface

**Partial today:** `ClarificationEvaluator` cross-checks stated-vs-actual per reply; the Risks page already has a watermelon flag (`RisksPage.tsx:36`). Make it a first-class, continuous signal.

- Add a scheduled **reconciliation workflow** (new `openprogram_drift_scan`, reuse the sync/workflow scaffolding) that, per active issue/developer, compares stated status vs Jira state + Git/PR activity and emits **Drift facts**: `said_done_no_pr`, `claimed_progress_no_activity`, `green_over_red` (a green parent hiding a red critical-path child).
- Enrich `DeveloperStatus` source/confidence from the drift outcome (human-confirmed vs contradicted vs inferred). Ties into Plan 02 C2 confidence.
- Surface on SM / PO / Exec dashboards + Risks page with drill-to-source-facts.

---

## Feature C — Trends / sparklines (and finally differentiate Manager vs Exec)

**Gap:** no time-series visualization anywhere, so Manager and Exec dashboards are byte-for-byte identical (`PersonaDashboard.tsx:70`).

- **Data:** `facts` and `node_statuses` are already Timescale hypertables. Add a daily rollup snapshot per node (or query historical `node_statuses`) and new persona endpoints, e.g. `GET /persona/{level}/{id}/trend?metric=rag&window=30d`.
- **Frontend:** a small `Sparkline` wrapper (ECharts, isolated per the "charts behind wrappers" standard) on cards; a trend line on the Manager view (momentum: "improving or sliding?") to genuinely distinguish it from the point-in-time Exec heatmap.
- Route chart colors through the CSS RAG tokens (fixes the hardcoded-hex mismatch noted in the frontend review).

---

## Feature D — Click-to-drill visualizations

- Wire `onEvents` click on `HeatmapChart` and `onNodeClick` on `HierarchyFlow` → navigate to the entity detail page / filter the dashboard. Backend drill endpoints already exist; this is a frontend wiring task that delivers the "drill from a red program indicator to the one blocking task" story the concept promises.

---

## Feature E — Escalation ladder

**Gap:** `nudge_number` is hardcoded to `1` everywhere (`status_collector.py:448,485,509,536`); only one nudge ever fires and there's no human escalation.

- Extend `dbos_nudge_workflow` / Temporal mirror to **N configurable steps** with escalating targets: dev reminder → notify SM → notify manager, driven by non-response and blocker age.
- Config: an **escalation policy** (steps, wait windows, targets) at tenant/pod level.
- Consult `AvailabilityService` (exists but unused before nudging) to suppress nudges during PTO/holidays/off-hours.
- Notifications to SM/manager go through the same `ChatProvider` port.

---

## Feature F — Scheduled narrative briefs

**Gap:** an ad-hoc "Ask the graph" box exists, but not the scheduled digests the concept describes.

- Reuse `ask_service` + `portfolio_feed_service` + the LLM to generate: **daily pod summary**, **weekly project update**, **exec brief** — on a schedule, delivered via chat and/or stored for the dashboard.
- New workflow + config for cadence/recipients per persona. Sourced/drillable (every claim links to facts), matching the explainability rule.

---

## Cross-cutting

- **Authorization:** new capabilities (`WRITE_ISSUE_TRACKER`) added centrally in `authorization.py`; default-deny preserved.
- **Testing:** unit + contract for `WriteBackService` gates and the Jira write payloads (respx); BDD for the consent conversation (yes / no / never / auto-apply); frontend tests would need a harness (none exists today — see Plan 04).
- **Privacy:** briefs and drift surfaces must not leak raw DM content into persona views (existing rule).

## Suggested sequencing

1. **Feature A (write-back)** — highest value; depends on Plan 02 **C4 identity mapping** (so the right Jira issues are targeted) and benefits from C2 (reliable `issue_updates` parsing).
2. **Feature B (drift)** + **C (trends)** — turn "reporting" into "insight"; C also fixes the Manager==Exec duplication.
3. **D (drill)**, **E (escalation)**, **F (briefs)** — independent, ship as capacity allows.
