# Privacy Changes

## Purpose

OpenProgram intentionally replaces manual status chasing with chat-based check-ins, structured status parsing, and rollups. That need existed before GenAI, but automation changes the privacy posture because the system can collect, retain, infer from, and redistribute status data at a scale that manual standups and ad hoc manager follow-ups usually do not.

This document records the current privacy-sensitive behavior, the issues it creates, why those issues matter for adoption, and the changes needed to resolve or minimize them.

## Current Status

- The product center is Phase 0 plus much of Phase 1 Sense: graph persistence, conversation persistence, read-only Jira/GitHub/calendar sync, proactive chat check-ins, reply parsing, non-response handling, rollups, role-scoped APIs, dashboards, and admin configuration.
- Developer status collection is chat-based. The system sends direct messages, receives replies through chat webhooks, parses replies, records status, and feeds persona dashboards.
- Raw inbound and outbound conversation turns are persisted in the durable conversation store with configurable retention.
- Raw replies are currently logged and added to traces in `StatusCollector.handle_reply` as `raw_reply` and `openprogram.raw_reply`.
- The observability design states that the legacy redaction processor is a compatibility no-op and does not remove message or token fields.
- Product requirements already say raw direct-message content should not be exposed through persona dashboards or public persona APIs.
- Role-based authorization exists as a central policy, but full SSO is explicitly out of scope for the current phase. Current user/profile administration is not production-grade identity management.
- Later roadmap items include more sensitive capabilities, including richer reconciliation, predictive risk, sentiment/overload signals, coaching, and natural-language portfolio queries.

## Core Issue

The risk is not that status collection exists. Manual status collection already happens through standups, Jira updates, Slack messages, and manager follow-ups.

The risk is that the automated system changes the operating model:

- **Scale:** it can ask every developer every day without a human bottleneck.
- **Persistence:** it can retain raw replies, inferred signals, confidence, blockers, logs, traces, and history.
- **Inference:** it can infer blockers, ambiguity, risk, confidence, mood, dependencies, and non-response patterns.
- **Audience expansion:** a reply intended for coordination can become management or executive rollup evidence.
- **Secondary use:** data collected for daily coordination can later be used for performance, sentiment, staffing, escalation, or trend analysis unless explicitly restricted.

If developers believe casual check-in replies are stored, analyzed, scored, and escalated, they will write defensively. That reduces honesty, which directly weakens the product's value.

## Issues And Fixes

### 1. Raw Replies In Logs And Traces

**How it happens**

`StatusCollector.handle_reply` logs the full reply text and writes it to the active trace as `openprogram.raw_reply`. The observability doc also says payload fields are retained unless callers explicitly omit them.

**Why it matters**

Logs and traces often have broader access than application data. They may be exported to third-party observability systems, retained longer than product data, copied into incidents, or visible to operators who should not see developer DM content.

**What to do**

- Remove raw reply text from logs and trace attributes.
- Log only safe metadata: tenant, developer id or pseudonymous subject id, correlation id, message id, parser outcome, and status transition.
- Replace `openprogram.raw_reply` with safe attributes such as `openprogram.reply_length`, `openprogram.reply_classification`, and `openprogram.has_blocker`.
- Make redaction real instead of a no-op.
- Add tests proving raw message text does not appear in logs, traces, or metrics.

### 2. Durable Raw Conversation Storage

**How it happens**

Raw inbound and outbound conversation turns are stored so the parser can use recent context and carry forward unresolved blockers.

**Why it matters**

Raw chat messages can contain personal details, frustration, third-party names, health context, security issues, HR-sensitive content, or confidential project information. Storing the full text increases breach impact and raises retention and access-control requirements.

**What to do**

- Store structured status signals by default: progress note, blockers, ETA change, confidence, source, and timestamps.
- Keep raw text only when there is a clear product need, with short retention and explicit access controls.
- Encrypt raw conversation content at rest separately from normal graph/status data.
- Add a purge job and tests that prove raw content expires according to retention settings.
- Provide a per-tenant retention setting with a conservative default.
- Keep conversation-history tools bounded by date, count, role, and purpose.

### 3. Inference Without Developer Visibility

**How it happens**

The system parses free text into structured status, carries forward blockers, asks clarifications, marks status stale or inferred, and later roadmap items add stronger reconciliation and prediction.

**Why it matters**

Inferences can be wrong. If inferred blockers, risk, mood, or confidence are shown upward without developer visibility, the system can feel like hidden scoring.

**What to do**

- Show developers the structured status extracted from their reply.
- Let developers correct or confirm parsed status before sensitive escalation.
- Label inferred, stale, confirmed, and system-derived status clearly.
- Keep confidence/source visible wherever rollups are shown.
- Never present inference as fact. Use wording such as "inferred from missing reply and recent facts" rather than "developer is blocked" when not human-confirmed.

### 4. Audience Expansion From DM To Management View

**How it happens**

The system rolls developer/task status up to pod, project, program, and portfolio views. Requirements allow some roles to see aggregates and mention raw DM access "where allowed."

**Why it matters**

A developer may be comfortable telling a scrum master "I am stuck waiting for API review" but not comfortable with the full raw message being visible to managers or executives. Adoption depends on predictable audience boundaries.

**What to do**

- Managers and executives should see aggregates, risk factors, and source links, not raw DM text.
- Scrum master access to raw text should be disabled by default and limited to break-glass or explicit team policy.
- Evidence shown in dashboards should be structured and redacted.
- Add field-level authorization tests for raw content, summaries, evidence snippets, and rollup factors.
- Record audit events whenever raw conversation content is accessed.

### 5. Identity And Authorization Are Not Production-Ready

**How it happens**

The architecture intentionally defers full SSO. The current phase has an auth seam, dev-mode principal support, and role-scoped APIs, but user/profile RBAC administration remains out of scope.

**Why it matters**

Privacy promises are only credible if identity, role assignment, and access review are production-grade. Demo role switching or config-driven principals are not enough for enterprise rollout.

**What to do**

- Add SSO/OIDC integration before production use.
- Add group/role mapping from identity provider claims.
- Add admin UI or configuration for role assignment review.
- Add audit logs for role changes, raw data access, export, and retention changes.
- Default-deny all new sensitive fields and endpoints.

### 6. LLM And Provider Data Handling

**How it happens**

Replies and recent conversation context are sent to parser/evaluator components through the LLM provider seam. Observability and LLM tracing can retain prompt payloads.

**Why it matters**

Even when the app itself is secure, prompts, traces, and third-party LLM provider logs can become another copy of sensitive developer communication.

**What to do**

- Minimize prompt payloads before calling the LLM.
- Prefer structured context over raw conversation text.
- Redact secrets, tokens, personal data, and irrelevant message fragments before LLM calls.
- Disable or redact prompt capture in LLM tracing for production.
- Document approved LLM providers, retention terms, and data-processing constraints.
- Add a provider mode that supports private/self-hosted LLM deployment where required.

### 7. Sentiment, Burnout, And People-Health Features

**How it happens**

Sentiment and overload signals are planned later roadmap items.

**Why it matters**

Individual sentiment or burnout scoring is highly likely to be perceived as employee monitoring. It can damage trust and create legal, HR, and works-council concerns depending on deployment region.

**What to do**

- Do not ship individual sentiment or burnout scores.
- If people-health features are added, keep them aggregate, team-level, thresholded, and privacy-reviewed.
- Do not use mood, sentiment, non-response, or after-hours signals for individual performance evaluation.
- Make people-health features opt-in at the tenant/team level and visible to employees.
- Document prohibited uses in product policy and admin setup.

## Recommended Priority Plan

### P0: Must Fix Before Any Real Pilot

- Remove raw replies from logs, traces, metrics, and error payloads.
- Implement real redaction for observability and LLM trace payloads.
- Add automated tests proving raw replies are not emitted outside approved storage.
- Set a conservative raw conversation retention default.
- Document what is stored, who can see it, and for how long.

### P1: Required For Enterprise Pilot

- Add SSO/OIDC and production role mapping.
- Add field-level authorization tests for raw content, evidence snippets, summaries, and rollups.
- Encrypt raw conversation content separately.
- Add audit events for raw-content access and sensitive policy changes.
- Add developer-visible parsed status with correction/confirmation.
- Add admin-visible privacy settings for retention, raw storage, and LLM trace capture.

### P2: Required Before Predict/Coach Expansion

- Add policy gates for any predictive, sentiment, or people-health feature.
- Limit people-health outputs to aggregate team-level signals.
- Add model-output review tests for harmful or overconfident inferences.
- Add documented prohibited-use policy: no individual performance scoring from check-ins, sentiment, or inferred mood.
- Add data export/delete support aligned with tenant policy.

## Acceptance Criteria

The privacy posture is acceptable for a pilot only when all of these are true:

- A realistic developer reply containing sensitive text does not appear in logs, traces, metrics, frontend payloads, or LLM traces.
- Raw conversation content has short, configurable retention and a tested purge path.
- Managers and executives cannot access raw DM content.
- Developers can see and correct the structured status inferred from their replies.
- Every displayed rollup identifies whether it is confirmed, inferred, stale, or system-derived.
- Production identity and role assignment are backed by SSO/OIDC, not demo role switching.
- Raw-content access is audited.
- Sentiment/burnout features are either absent or aggregate-only with explicit policy controls.

## Product Positioning Guidance

The product should not be positioned as developer monitoring. It should be positioned as:

> "A privacy-safe delivery coordination system that reduces status meetings, prevents silence from becoming fake green, and gives leaders explainable rollups without exposing raw developer conversations."

That positioning is credible only if the implementation enforces it technically.
