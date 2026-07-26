# Plan 01 — Reliability (Slack reply ingestion)

## Objective

Make the inbound chat reply path production-grade:

1. **Fast ack + async processing.** Accept the Slack event, return `200` immediately (well inside Slack's ~3s deadline), and process it asynchronously and durably on the **configured** workflow engine (DBOS or Temporal), with error handling and automatic retries.
2. **Burst coalescing.** A developer often sends several messages in a row. Wait **at least 30 seconds from the last message received** for a conversation before processing, then process **all** buffered messages together as one reply.

This closes the four reliability findings from the assessment:

| ID | Problem | Current location |
|---|---|---|
| R1 | Webhook runs 1–5 LLM calls synchronously; blows Slack's 3s ack → redeliveries | `api/routers/webhooks.py:46` → `infra/registry.py:283` → `core/application/status_collector.py:233,270` |
| R2 | No dedup of Slack retries / `event_id` | `api/routers/webhooks.py`, `infra/adapters/chat/slack.py` mapper |
| R3 | Reply lost on transient LLM failure: user turn is persisted (`status_collector.py:203`) **before** the classify call (`:233`); on failure the webhook 500s but retries hit `user_turn_exists` → `"ignored"` and the reply is stuck until the hours-later timeout | `core/application/status_collector.py:196,203,233` |
| R4 | Morning fanout is fully sequential (`await handle.get_result()` per developer) | `infra/workflows/checkin_fanout.py:29`, `infra/adapters/workflows/dbos.py:342` |

> **Prerequisite (security):** we are rewriting the webhook, so fold in the signature-verification fix here. `chat_webhook_signature_valid` (`infra/registry.py:273`) returns `True` for any provider that isn't literally `"slack"`, while the mapper treats `mock_slack` as Slack (`infra/adapters/catalog.py:113`). Verify the signature whenever the **configured** chat provider is Slack, and reject unknown provider paths, rather than keying off the URL segment.

---

## Design overview

Split the path into **(A) fast synchronous intake** and **(B) durable async processing**, connected by a **durable inbound-event buffer** and a **per-conversation debounce/coalesce workflow**, with **(C) a sweeper safety net** and **(D) parallel fanout**.

```
Slack ──POST /webhooks/chat/slack──▶ [A. Fast intake]
                                        │  verify sig · dedup on event_id · persist row · arm workflow · 200
                                        ▼
                              inbound_chat_events (durable buffer)
                                        │
                                        ▼
                         [B. openprogram_reply_coalesce] per conversation
                            recv/​signal loop → 30s quiet → drain step (retryable)
                                        │
                                        ▼
                     ReplyIngestionService → existing handle_reply pipeline
                                        ▲
              [C. inbound_events_sweeper cron] re-arms anything stuck (processed_at IS NULL)
```

### A. Fast intake — `POST /webhooks/chat/{provider}` (no LLM; target p99 < 1s)

Rewrite `chat_webhook` (`api/routers/webhooks.py`) so it does **only** cheap work:

1. Verify signature (fixed per prerequisite above).
2. Handle `url_verification` challenge (keep existing shortcut, `webhooks.py:41`).
3. Map the minimal envelope with the existing `SlackChatWebhookMapper` (drops bot/self/subtype, already in `slack.py`). Extract `event_id` (Slack `event_id` at the envelope top level) and `X-Slack-Retry-Num`.
4. Compute `conversation_key = f"{tenant_id}:{channel}"` (DM channel); include thread ts if threaded.
5. `INSERT INTO inbound_chat_events (...) ON CONFLICT (tenant_id, provider, event_id) DO NOTHING`. If no row inserted → **duplicate**, return `200 {"status":"duplicate"}` (R2). Slack retries carry the same `event_id`, so this also absorbs the redeliveries R1 currently causes.
6. **Arm** the coalesce workflow for `conversation_key` (idempotent create) and **signal** it that a new event arrived (resets the 30s timer).
7. Return `200 {"status":"accepted"}`.

No correlation resolution, no `handle_reply`, no LLM in this path.

### B. Durable coalesce/debounce workflow — `openprogram_reply_coalesce`

One workflow instance **per conversation**, keyed by `conversation_key`. It waits for quiet, then drains.

**Debounce (reset-on-message timer):**

- **DBOS mapping** (mirror the existing `dbos_nudge_workflow` timer style, `dbos.py:356`):
  ```
  while True:
      got = await DBOS.recv(topic="reply", timeout_seconds=settings.reply_debounce_seconds)  # 30
      if got is None:      # 30s of quiet
          break
      # a new event arrived → loop, timer resets
  await drain_step(conversation_key)
  ```
  Intake side: `with SetWorkflowID(coalesce_id): await DBOS.start_workflow_async(...)` (idempotent — no-op if already running) **then** `DBOS.send(coalesce_id, "ping", topic="reply")`.
- **Temporal mapping:** use `signal_with_start(workflow_id=coalesce_id, signal="new_event")` (the canonical debounce primitive). Workflow loop: `while await workflow.wait_condition(lambda: self._pending, timeout=30s): self._pending = False` → on timeout, drain. Use `continue_as_new` for very long-lived conversations.

**Drain step** (`@DBOS.step(retries_allowed=True)` / Temporal activity with retry policy):

1. Load all `inbound_chat_events` for `conversation_key` where `processed_at IS NULL`, ordered by `received_at`.
2. Resolve correlation **once** via existing `resolve_reply_correlation` (`status_collector.py:336`) using the latest event's user/thread/ts.
3. Coalesce message texts (newline-join, oldest→newest) into one combined reply string.
4. Call the refactored `ReplyIngestionService` (below) with the combined text + the list of source event ids.
5. Mark all drained events `processed_at = now` **in the same transaction** as the finalize write where possible.

**Complete-vs-race guard:** before the workflow returns, re-query for `processed_at IS NULL` events on the conversation (arrived during drain). If any exist, loop again instead of completing. The sweeper (C) is the belt-and-suspenders backstop.

### C. Safety-net sweeper — `openprogram_inbound_events_sweeper` (cron)

A scheduled workflow (e.g. `*/5 * * * *`) that finds `inbound_chat_events` with `processed_at IS NULL AND received_at < now() - grace` and re-arms the coalesce workflow for those conversations. This **guarantees no reply is ever permanently stuck** — even if a signal is dropped, the worker crashed mid-drain, or an LLM step exhausted retries. This is the durable replacement for R3's "stuck until the timeout workflow" behavior, and it composes with Plan 04's dead-letter/alerting.

### D. Parallel fanout (R4)

`_run_dbos_checkin_fanout` (`dbos.py:302`) currently calls `_start_daily_checkin_workflow`, which `await handle.get_result()` per developer (`:342`), serializing the whole morning. Change the **fanout** path to start all child check-in workflows without blocking on each result:

- Start every child with its deterministic `SetWorkflowID`, collect handles, then `await asyncio.gather(*[h.get_result() for h in handles])` (or return the workflow ids without awaiting — fire-and-forget durable). Keep the "drive to completion" behaviour only where a caller needs the outbound message to exist synchronously (admin single-dispatch).
- Temporal mirror: start children concurrently via `asyncio.gather`.
- Add a bounded concurrency limit (`settings.checkin_fanout_concurrency`, default e.g. 10) to avoid hammering Slack rate limits.

---

## Data model

New table (Alembic migration under `backend/infra/persistence/migrations/versions/`):

```
inbound_chat_events
  id              uuid pk
  tenant_id       text
  provider        text
  event_id        text           -- Slack event_id
  conversation_key text
  chat_user_ref   text
  chat_thread_ref text null
  outbound_message_id text null
  correlation_id  text null
  text            text           -- extracted message text (redaction-aware; see privacy)
  raw_payload     jsonb
  received_at     timestamptz
  processed_at    timestamptz null
  attempts        int default 0
  UNIQUE (tenant_id, provider, event_id)
  INDEX (conversation_key, processed_at)
  INDEX (processed_at, received_at)
```

- Consider a TimescaleDB hypertable + retention like `conversation_turns` (they already purge via `conversation_purge`). Add an equivalent purge for processed rows.
- **Privacy:** `text`/`raw_payload` contain raw DM content — keep them out of logs/traces/persona views (same rule the codebase already enforces for `conversation_turns`). Extend the existing purge/retention workflow.

New port + adapters:

- `core/ports/repositories.py` → `InboundChatEventRepository` (append, list_unprocessed_for_conversation, mark_processed, list_stuck).
- `infra/persistence/postgres_*` + `in_memory_graph.py` implementations; `tests/contract/fakes.py` fake; add a contract test in `tests/contract/contracts.py`.

---

## Ports / application / config

- **New application use case** `core/application/reply_ingestion.py::ReplyIngestionService` — thin orchestration that accepts a coalesced reply (combined text + source event ids) and calls the existing `StatusCollector.handle_reply` logic. Keeps the LLM/parse logic in `core`, engine-agnostic (hexagonal purity preserved; import-linter stays green).
- **Refactor** `StatusCollector.handle_reply` so recording the user turn is **keyed by `event_id`** (idempotent) and the classify/clarify/finalize effects are individually retry-safe. This removes R3 at the source: the turn insert and the classify call are no longer an unguarded "record-then-maybe-fail" pair — the whole drain is a retryable durable step and events stay `processed_at IS NULL` until the drain fully succeeds.
- **Extend `WorkflowScheduler` port** (`core/ports/workflows.py`) with `arm_reply_coalesce(conversation_key, tenant_id)` and `ensure_inbound_sweeper_schedule(config)`; implement in both `dbos.py` and `temporal.py`; register the new workflows in `infra/workflows/schedule.py` and `worker.py`.
- **Registry:** replace the webhook's `process_chat_webhook` call with `enqueue_inbound_chat_event(...)` (persist + arm). Keep `process_chat_webhook` as the drain-time entry point.
- **Settings** (`config/settings.py`):
  - `reply_debounce_seconds: int = 30`
  - `reply_processing_max_retries: int`, `reply_processing_retry_backoff_seconds`
  - `inbound_events_sweeper_cron: str`, `inbound_events_grace_seconds: int`
  - `checkin_fanout_concurrency: int = 10`
  - `slack_fast_ack_enabled: bool = True` — feature flag to fall back to the legacy synchronous path during rollout.

---

## Testing

- **Unit:** debounce loop with a fake clock (N messages within window → single drain; quiet → drain); coalescing order; dedup on `event_id`; correlation resolution on coalesced text; parallel-fanout start order.
- **Contract:** `InboundChatEventRepository` (postgres + fake parity).
- **Integration** (`tests/integration/`, testcontainers): webhook returns `200` in < X ms with no LLM invoked; three rapid messages → one processing pass consuming all three; forced LLM step failure → retried and eventually processed, never stuck; Slack retry with same `event_id` → no reprocess, no duplicate DM.
- **BDD** (`tests/bdd/`, extend the mock-slack `.feature` files): burst-of-messages scenario, retry scenario, transient-failure-recovery scenario. The Mock Slack simulator already exercises the webhook-correlation path, so these are natural extensions.

---

## Rollout

1. Ship table + repository + sweeper workflow (no behaviour change; nothing arms the workflow yet).
2. Ship fast-intake webhook + coalesce workflow behind `slack_fast_ack_enabled`. Legacy synchronous path remains as fallback.
3. Enable in local + Mock Slack; validate the new BDD scenarios.
4. Enable in container; watch webhook latency, redelivery counts, stuck-event gauge.
5. Land the parallel-fanout change (independent; can ship earlier).

---

## Risks & mitigations

- **DBOS `recv`/`send` semantics around a just-completed workflow** (signal to a completed instance) → mitigated by the drain re-check loop **and** the sweeper.
- **Correlation ambiguity across a burst spanning two check-ins** → keep the conservative "don't guess" ambiguity handling documented in `checin.md`; the coalescer resolves correlation once and defers ambiguous bursts to clarification rather than mis-routing.
- **Extra write volume** (one row per inbound event) → retention/purge like `conversation_turns`.
- **Ordering** — coalesce strictly by `received_at`; Slack `ts` is a reliable tiebreaker.

## Acceptance criteria

- p99 webhook ack < 1s with **no** LLM call in the request path.
- 3 messages within 30s → **exactly one** processing pass that uses all 3.
- Duplicate Slack delivery (same `event_id`, or `X-Slack-Retry-Num` set) → no duplicate processing and no duplicate DM.
- Forced LLM failure on the reply path → reply is processed after retry; it is **never** left permanently `"ignored"`.
- Morning fanout dispatches N developers concurrently (bounded), not serially.
