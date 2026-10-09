# Guardrails

What OpenProgram's agent may read, say, do and spend, and what stops it. Status as of 2026-10-09.

## Principles

1. **The model proposes, code decides.** The model reads replies and drafts words. Application code chooses every message, every recipient and every tracker write, and checks the model's output before using it.
2. **Guard where the effect leaves the system.** Each guardrail sits in the adapter that makes the call (LLM, chat), so no caller can skip it.
3. **A refusal fails loudly.** A refused call raises `ProviderUnavailable`, the error callers already handle when a provider is down. Nothing is recorded as sent when it wasn't.
4. **Circuit breakers, not quotas.** Defaults are on and set far above a normal day. They stop runaway loops, not normal use. `0` turns each one off.

## In place

New in this change: ★

| Area | Guardrail | Where |
|---|---|---|
| **Model input** | ★ A request carrying more than 400k characters is refused before it is sent | `infra/adapters/llm/guard.py` |
| | Jira text sent to the gate finder is fenced, and every item must quote it word for word | `gate_extraction.py` |
| **Model output** | JSON is parsed and each field type-checked. An unparseable reply never finalizes a check-in as healthy | `status_parsing.py` |
| | Briefs are checked sentence by sentence against facts. Unbacked names, keys and claims are dropped | `brief_grounding.py` |
| | ★ A drafted follow-up with a link, chat markup or over 320 characters becomes the generic question | `status_parsing.py` |
| | ★ The note a teammate is DMed about a request has its links and markup taken out | `cross_person_service.py` |
| | ★ A composed check-in or nudge that carries a link or chat markup falls back to the template. This adds to the existing length and prompt-echo checks | `status_collector.py` |
| | ★ Slack never posts `@channel`, `@here`, `@everyone` or a group mention | `slack_text.py` |
| **Tools** | Read-only, scoped to the developer by the server, at most 3 rounds | `core/application/tools/`, `tool_loop.py` |
| | ★ At most 8 tool calls a turn; each result is cut to 12k characters | `tool_loop.py` |
| **Jira writes** | Off by default. When on, a write needs the member's consent, is made only for the issue's assignee and is held while an MR is open. It moves the issue only to a canonical state, posts only a templated comment, and is audited and revertible | `writeback_service.py` |
| **Volume and cost** | Send-once per message key; at most 1 DM per second per person | `send_once.py`, `rate_limit.py` |
| | ★ At most 100 DMs per person per UTC day | `infra/adapters/chat/send_cap.py` |
| | ★ At most 10M tokens per tenant per UTC day | `infra/adapters/llm/guard.py` |
| **Access and data** | Dev auth and the public secret key are refused outside `environment=local` | `config/settings.py` |
| | ★ An admin can dispatch workflows only for their own tenant | `api/routers/admin.py` |
| | ★ A saved secret is sent only to the address and sign-in it was saved with (every `routes_secrets` field). A test with one changed refuses to reuse it; a save with one changed needs the secret entered again and drops secrets kept for another sign-in method | `connection_service.py` |
| | Secrets are encrypted and never returned. Raw DM content is never returned by the API. Conversations are kept 30 days | existing |

## Decisions

- **D1. Enforce in the adapters** (`LiteLlmProvider`, Slack and mock-Slack adapters), not at each call site and not as wrappers. There is one place to change, and the built adapter types stay as they were. The fake LLM and the fake chat provider used in tests have no guards.
- **D2. A refusal is a provider outage.** Briefs and gate scans fall back to their deterministic output. A check-in reply's step fails and retries. The reply stays in the inbound queue and dead-letters if it never succeeds, where it can be re-armed. Ask returns 503 (budget used up) or 413 (question too long).
- **D3. Counts are per UTC day and shared through Redis.** In memory mode they are per process. The DM cap counts after send-once, so a retry that returns an earlier message is not counted again. If Redis can't be reached, the check fails and nothing is sent, the same as other Redis-backed sends.
- **D4. Model-drafted words never carry a link or a mention.** A link from the bot looks trusted. Where fixed text can stand in (questions, composed DMs), it replaces the drafted text. Where it can't (a request's note), links and markup are taken out instead.
- **D5. No prompt rewrite to fence untrusted text, for now.** The prompts are tuned against live QA rounds, and a change needs an eval set to prove it. The risk is contained without it: the model cannot act on its own, its output is validated, and writes are deterministic and consented.
- **D6. No global kill switch in this change.** A switch that doesn't need a restart must be persisted per tenant and enforced on every send path; that is next on the list. Until then, use the runbook below.

## Stopping the bot now

| To stop | Do |
|---|---|
| Everything scheduled or reply-driven | `docker compose stop worker`. Replies wait in the inbound queue. On restart, overdue nudges fire at once. Admin "send now" in the API still works |
| One feature | Set its env flag false and restart: `ESCALATION_ENABLED`, `CROSS_PERSON_AUTO_NOTIFY`, `DAY_REPORT_ENABLED`, `CHECKIN_ACK_ENABLED`, `NARRATIVE_BRIEF_ENABLED`, `GATE_TEXT_MODEL_ENABLED` (all with the `OPENPROGRAM_` prefix) |
| Jira writes | Admin → write-back switch off. Takes effect at once, no restart |
| Model calls | `OPENPROGRAM_LLM_DAILY_TOKEN_BUDGET=1` and restart: at most one more call per tenant a day. Never `LLM_PROVIDER=fake`, which records canned statuses |

## Settings and signals

| Setting | Default | Off |
|---|---|---|
| `OPENPROGRAM_LLM_DAILY_TOKEN_BUDGET` | `10000000` | `0` |
| `OPENPROGRAM_LLM_MAX_REQUEST_CHARS` | `400000` | `0` |
| `OPENPROGRAM_CHAT_DAILY_DM_LIMIT` | `100` | `0` |

Alert on these log events: `llm_call_refused` (with a `guardrail` field), `chat_daily_dm_limit_reached`, `tool_loop_call_limit_reached` and `tool_loop_cap_reached`.

## Open risks, most important first

1. **`environment` defaults to `local`.** A local environment allows dev auth, which makes every visitor an admin without signing in. Any stack reachable beyond localhost must set `environment` to something other than `local` and use OIDC. Next step: require `environment` explicitly in container mode.
2. **No per-tenant pause switch.** See D6.
3. **Recipients aren't checked against the directory.** Former members still get check-ins.
4. **No quiet hours.** PTO is honoured only on the developer nudge, not on the first check-in or escalations.
5. **The Slack signature check is skipped** unless `chat_provider=slack` in container mode, and nothing refuses `mock_slack` outside local.
6. **Connector URLs and the Teams webhook can point at private addresses** (SSRF). Report emails can go to any domain.
7. **Untrusted text isn't fenced in prompts** (D5). Blocker text the model wrote earlier is fed back into later prompts.
8. **Ask is scoped by role only, not per pod or project,** and its answers aren't grounded.
9. **Write-back revert doesn't check whether the issue moved since.** The transition is made before its audit row is written.
10. **The API has no request-size limit, inbound rate limit or security headers.**
11. **The link filter catches `http(s)://` and `www.` only.** A bare `evil.example/login` passes.
