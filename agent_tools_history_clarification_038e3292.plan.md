---
name: agent tools history clarification
overview: Add an LLM tool-calling seam so the agent can fetch more/older conversation history on demand (read-only tool over the existing repository), and add a bounded clarification loop where the LLM decides/drafts follow-up questions while the actual DM send stays deterministic and auditable. No MCP; everything stays behind existing ports.
todos:
  - id: llm-seam
    content: Extend LlmRequest/LlmResponse with tools/tool_calls/tool_results + finish_reason (core/domain/llm.py), update LiteLlmProvider payload+parsing, and make FakeLlmProvider scriptable.
    status: completed
  - id: history-tool
    content: Add AgentTool port, ConversationHistoryTool over ConversationRepository (since/limit/day), and a ToolCallingAgent loop with llm_max_tool_iterations; route compose/parse through it.
    status: completed
  - id: clarification-domain
    content: Add CheckInClarification domain + StatusRepository methods + postgres/in-memory impls + migration 0006 + checkin_max_clarifications setting.
    status: completed
  - id: clarification-loop
    content: "Add ClarificationEvaluator and rewrite handle_reply: dedupe by message_id, evaluate, send clarifying DM deterministically while keeping the check-in open, finalize on sufficient/cap; add clarifying webhook status."
    status: completed
  - id: timeout-wiring-tests
    content: Finalize in-progress clarifications on reply-wait timeout, wire tool+agent+settings in registry, update fakes, and add unit/contract tests; run ruff + type-check + suites.
    status: completed
isProject: false
---

# Agent History Tool + Clarification Loop

## Goal

Two capabilities, hybrid approach (confirmed): reads become a real LLM-callable tool; the clarification send stays deterministic code with the LLM only deciding/drafting. Chronological history only (no pgvector yet). No MCP — capabilities stay behind existing ports.

## Current state (why this is needed)

- The LLM seam is text-only: [backend/core/ports/llm.py](backend/core/ports/llm.py) exposes `complete(LlmRequest) -> LlmResponse`; `LlmRequest`/`LlmResponse` in [backend/core/domain/llm.py](backend/core/domain/llm.py) have no `tools`/`tool_calls`. The model cannot call anything.
- History is an eager fixed window: `StatusCollector._recent_conversation_turns` uses `RECENT_CONVERSATION_TURN_LIMIT = 20` (`list_recent_turns`) in [backend/core/application/status_collector.py](backend/core/application/status_collector.py). The model can't ask for more/older.
- Check-in is one-shot: `handle_reply` finalizes on the first reply (sets `replied_at`, records `DeveloperStatus`, consumes the correlation). The webhook then treats any further message as a duplicate ([backend/api/routers/webhooks.py](backend/api/routers/webhooks.py)). No clarification is possible.
- LiteLLM adapter already posts OpenAI-compatible `/v1/chat/completions` ([backend/infra/adapters/llm/litellm_provider.py](backend/infra/adapters/llm/litellm_provider.py)), so native `tools`/`tool_calls` are a payload extension, not a rewrite.

## Design overview

```mermaid
sequenceDiagram
    participant WH as Webhook
    participant SC as StatusCollector.handle_reply
    participant EV as ClarificationEvaluator (LLM + tools)
    participant CR as ConversationRepository
    participant CH as ChatProvider
    WH->>SC: inbound reply (correlation resolved)
    SC->>CR: append user turn (dedupe by message_id)
    SC->>EV: evaluate(turns) [LLM may call fetch_history tool]
    EV->>CR: fetch_history(since/limit) read-only
    EV-->>SC: sufficient(signals) OR needs_clarification(question)
    alt needs_clarification AND under cap
        SC->>CH: send_dm(question)  [deterministic, logged, idempotent]
        SC->>CR: append agent turn; keep correlation OPEN
    else sufficient OR cap reached
        SC->>SC: finalize (replied_at, status, consume correlation)
    end
```

Decision rule preserved: reads are model-driven (safe, idempotent); the clarification send is deterministic code. An in-progress clarification keeps `replied_at = None` and the correlation unconsumed, so the existing webhook duplicate-check and reply-resolution keep working unchanged.

## Phase 1 — LLM tool-calling seam

- Extend [backend/core/domain/llm.py](backend/core/domain/llm.py): add `LlmTool` (name, description, JSON-schema `parameters`), `LlmToolCall` (id, name, arguments), `LlmToolResult` (tool_call_id, content); add `tools: tuple[LlmTool, ...] = ()` and `tool_results: tuple[LlmToolResult, ...] = ()` to `LlmRequest`; add `tool_calls: tuple[LlmToolCall, ...] = ()` and `finish_reason: str | None` to `LlmResponse`. Keep `LlmMessage` for prior turns.
- Update [backend/infra/adapters/llm/litellm_provider.py](backend/infra/adapters/llm/litellm_provider.py): include `tools` in the request body, emit `tool`-role messages for `tool_results`, and parse `choices[0].message.tool_calls` + `finish_reason`. Trace sink hashing unchanged (still no raw prompt logged).
- Update fake [backend/infra/adapters/llm/fake.py](backend/infra/adapters/llm/fake.py): allow a scripted queue of responses so a test can return a `tool_calls` response then a final text/JSON response.
- Port [backend/core/ports/llm.py](backend/core/ports/llm.py) signature stays the same (tools ride on the request).
- Tests: extend [backend/tests/unit/test_litellm_provider.py](backend/tests/unit/test_litellm_provider.py) for tool payload round-trip; contract `complete` behavior unchanged when `tools=()`.

## Phase 2 — Conversation history tool + tool loop

- New `AgentTool` port in `backend/core/ports/tools.py`: `name`, `description`, `parameters` schema, `async def run(arguments: Mapping[str, JsonScalar]) -> str`.
- New `backend/core/application/tools/conversation_history.py`: `ConversationHistoryTool` bound to `(tenant_id, developer_id)`, wrapping `ConversationRepository`. Args: `since_days` and/or `limit` (and `on` date), mapped to existing `list_recent_turns(..., since=...)` / `list_turns_for_day`. Returns formatted turns for the model only; never logged or surfaced (consistent with the no-raw-DM-in-logs/persona constraint). Naturally bounded by `conversation_retention_days`.
- New `backend/core/application/agents/tool_loop.py`: `ToolCallingAgent.run(request, tools)` loop — call `complete`; if `tool_calls`, execute each via the tool registry, append `tool_results`, re-call; stop at no-tool-calls or `llm_max_tool_iterations`. Each iteration traced.
- Replace the eager window: `StatusCollector` keeps a small recent-N seed but routes compose/parse/evaluate through `ToolCallingAgent` with `ConversationHistoryTool` available so the model fetches more only when needed.
- Config in [backend/config/settings.py](backend/config/settings.py): `llm_max_tool_iterations: int = 3`.
- Tests: unit tests for the tool (read-only, since/limit) and the loop (executes tool call, terminates at cap), using `FakeConversationRepository` + scripted `FakeLlmProvider`.

## Phase 3 — Clarification loop (decide/draft by LLM, send by code)

- Domain: add `CheckInClarification` to [backend/core/domain/status.py](backend/core/domain/status.py) (mirrors `CheckInNudge`: `tenant_id`, `correlation_id`, `clarification_number`, `question`, `sent_at`, `outbound_message_id`).
- Ports: add `record_checkin_clarification(...) -> CheckInClarification` and `checkin_clarification_count(tenant_id, correlation_id)` to `StatusRepository` in [backend/core/ports/repositories.py](backend/core/ports/repositories.py).
- Persistence: implement in [backend/infra/persistence/postgres_status.py](backend/infra/persistence/postgres_status.py) and the in-memory store; new migration `backend/infra/persistence/migrations/versions/0006_checkin_clarifications.py` (table keyed by `(tenant_id, correlation_id, clarification_number)`, idempotent like nudges).
- Evaluator: new `ClarificationEvaluator` (in [backend/core/application/status_parsing.py](backend/core/application/status_parsing.py) or a sibling) — one structured LLM call (history tool available) returning `{ sufficient: bool, question: str | null, signals: {...} | null }`.
- Rewrite `handle_reply` in [backend/core/application/status_collector.py](backend/core/application/status_collector.py):
  - Dedupe inbound by `chat_message_id` (skip if already recorded as a USER turn) for webhook-redelivery safety.
  - Append user turn (as today), then run the evaluator over the conversation turns for this correlation.
  - If `needs_clarification` and `clarification_count < checkin_max_clarifications`: record clarification (idempotent), compose+`send_dm` deterministically (logged, capped), append agent turn, leave `replied_at = None` and correlation unconsumed; return a pending result.
  - Else (sufficient, or cap reached): finalize exactly as today (record `CheckIn.replied_at`, `DeveloperStatus`, consume correlation, append redacted fact). On cap-reached-without-sufficiency, finalize with best-effort signals plus a note.
- Webhook [backend/api/routers/webhooks.py](backend/api/routers/webhooks.py): unchanged duplicate guard (still keys off `replied_at`); add a `clarifying` status to `ChatWebhookResponse` for the pending path.
- Config [backend/config/settings.py](backend/config/settings.py): `checkin_max_clarifications: int = 2`.

## Phase 4 — Timeout finalization + wiring + tests

- Ensure the reply-wait/nudge timeout finalizes an in-progress clarification instead of leaving it open: in `record_non_response`/the nudge path, if turns exist for the correlation, finalize best-effort from accumulated turns rather than `UNKNOWN` ([backend/infra/workflows/nudge.py](backend/infra/workflows/nudge.py), [backend/core/application/status_collector.py](backend/core/application/status_collector.py)).
- Wire dependencies in [backend/infra/registry.py](backend/infra/registry.py): build `ConversationHistoryTool` + `ToolCallingAgent` and pass into `StatusCollector` (and `StatusParser`/evaluator); thread new settings.
- Update fakes/contract tests in [backend/tests/contract/fakes.py](backend/tests/contract/fakes.py) (`FakeStatusRepository` clarification methods) and add unit tests in [backend/tests/unit/test_status_collector.py](backend/tests/unit/test_status_collector.py): sufficient reply finalizes once; ambiguous reply triggers one clarifying DM and stays open; cap reached finalizes; duplicate message_id is ignored.
- Run `ruff`, type-check, and the unit/contract suites.

## Notes / constraints

- This intentionally changes the long-standing one-shot finalization invariant. Per the repo's Lore protocol, the commit should record a `Constraint`/`Directive` capturing the new "clarification keeps the check-in open until sufficient or capped" rule, and run `lore constraints/rejected/directives` on the touched files before editing.
- No new privacy posture: history content is already persisted (migration 0005) and already sent to the model; the tool only re-reads it and is retention-bounded; tool output and clarifying questions are never logged or exposed via persona DTOs.
- MCP deliberately excluded: chat is already a `ChatProvider` adapter and the conversation store is a local port; MCP would add a network/governance hop with no benefit here.

## Open follow-ups (out of scope now)

- pgvector semantic recall of older turns (chosen out for now).
- Optional Redis ephemeral session buffer — not needed since turns are already persisted per `correlation_id`; reconstructed from the conversation repository instead.
