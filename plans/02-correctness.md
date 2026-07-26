# Plan 02 — Correctness

## Objective

Eliminate the ways the system can silently record a **wrong** status or fire check-ins at the **wrong time**. Four defects plus a set of locale/heuristic fragilities.

| ID | Problem | Current location |
|---|---|---|
| C1 | Incoherent timezone model: "checked in today?" uses **UTC** day boundaries while the collector uses the developer's **local** day; per-developer timezone is unvalidated and silently falls back to UTC; scheduling is DST-fragile | `infra/persistence/postgres_status.py:544`, `core/application/status_collector.py:1729,1733`, `infra/workflows/daily_checkin.py:274,276`, `api/dtos.py:1303` |
| C2 | Fragile LLM output handling: bare `json.loads`, no markdown-fence tolerance, and **dangerous silent fallbacks** — a garbled parse defaults clarify to `sufficient=True` (finalizes as healthy) and parse to `progress_note = raw_reply` | `core/application/status_parsing.py:84,95,152,163` |
| C3 | `idempotency_key` is dead metadata: set on 5 outbound messages but the real Slack adapter ignores `message.metadata`, so a step retry after send-before-record re-sends the DM | `core/application/status_collector.py:510,835,886`, `infra/adapters/chat/slack.py:83` |
| C4 | Identity mapping gap (UAT-D1-003, P0): Jira `accountId` ≠ Slack user id, so `build_context` queries Jira by Slack id and finds nothing — check-in DMs can't reference the developer's real active issues | `core/application/status_collector.py` `build_context`; `infra/adapters/jira/jira_adapter.py:71` `list_active_for` |

---

## C1 — Coherent timezone / DST model

**Root cause:** two different notions of "today." `developers_without_checkin` (`postgres_status.py:544`) compares `replied_at` against `as_of::date … < +1 day` in UTC, while the collector computes `checkin_date` in the developer's local zone (`_local_date`, `status_collector.py:1729`). A developer whose local day ≠ UTC day gets wrongly nudged, double-counted, or skipped. The brute-force `_candidate_correlation_dates` (`utc_date ± 1 day`, `status_collector.py:1707`) is a symptom of papering over this.

**Fix — make local check-in date authoritative and stored:**

1. **Domain helper.** Add a single pure helper `core/domain/status.py::local_date(instant, tz)` and a `resolve_timezone(developer_pref, tenant_default) -> ZoneInfo` used everywhere a "local day" is needed. One code path, not three.
2. **Persist `checkin_date` (local date) on the check-in.** The `checkins` table should store the developer-local `checkin_date` (it is already conceptually per-person/per-local-day, per `checin.md`). Add the column if absent; backfill from `asked_at` + the developer's tz.
3. **Rewrite `developers_without_checkin`** to test "has a check-in for this **local** `checkin_date`" by comparing the stored `checkin_date` column, not UTC boundaries on `replied_at`. Deterministic and tz-correct.
4. **Retire / narrow `_candidate_correlation_dates`.** Once `checkin_date` is authoritative, correlation resolution keys off the developer's local date directly; keep at most a ±1-day fallback for clock skew, not as the primary mechanism.

**Fix — validate timezones at the edge:**

5. Add a Pydantic validator on `CheckinPreferenceUpdateRequest.timezone` (`api/dtos.py:1303`) — reject anything not in `zoneinfo.available_timezones()` with `422`, mirroring how `weekdays` is already validated (`:1308`). Validate the tenant default at settings load. No more silent UTC fallback masking a typo.

**Fix — DST-safe scheduling:**

6. In `_scheduled_at` (`daily_checkin.py:276`), stop relying on the implicit `fold=0`. For spring-forward **gaps** (the wall-clock time doesn't exist), advance to the next valid instant; for fall-back **ambiguity**, pick a defined fold deliberately. Add unit tests around both US and EU DST transition dates.

**Acceptance:** a developer in `Asia/Kolkata` and one in `America/Los_Angeles` are each evaluated against their own local day; an invalid timezone is rejected at the API; check-ins land at the correct wall-clock time across a DST boundary.

---

## C2 — Trustworthy structured output

**Root cause:** `parse_reply` and `evaluate` do `json.loads(response.text)` with no fence stripping, and on `JSONDecodeError`:
- `parse_reply` → `CheckInSignals(progress_note=raw_reply)` (`status_parsing.py:95`) — loses blockers/ETA;
- `evaluate` → `ClarificationDecision(sufficient=True)` (`:163`) — **finalizes a possibly-blocked check-in as sufficient/healthy.**

A model that wraps JSON in ```json fences (common) silently degrades to these fallbacks.

**Fixes:**

1. **Enforce JSON mode.** Add a `response_format` / `json: bool` field to `core/domain/llm.py::LlmRequest`; in `LiteLlmProvider.complete` (`infra/adapters/llm/litellm_provider.py:128`) include `response_format={"type":"json_object"}` when set. Also pass `temperature=0` and a `max_tokens` for determinism and cost (currently none are sent).
2. **Fence-tolerant, retrying parse.** Add `core/application/json_parsing.py::extract_json_object(text)` — strips ```json fences / prose, extracts the first balanced `{...}`, then `json.loads`. On failure, **retry the LLM once** with a terse "return only valid JSON" reminder before any fallback.
3. **Make the fallback safe (the important one).** On persistent parse failure:
   - `evaluate` must **not** default to `sufficient=True`. Default to `sufficient=False` with a generic clarification (or route to a `needs_review` status), so a garbled parse **never silently becomes healthy**.
   - `parse_reply` may keep `progress_note=raw_reply` but must set `blockers_answered=False` / low parser confidence so the status **cannot roll up green** on unparsed content.
4. **Confidence field.** Thread a parser-confidence signal into `DeveloperStatus.source`/confidence so downstream rollups and the UI reflect "unverified/low-confidence" (ties into Plan 03 drift/confidence).

**Acceptance:** a fenced-JSON model reply parses correctly; a deliberately corrupt LLM response results in a clarification or `needs_review`, never a green "sufficient" finalize; parse settings are deterministic (`temperature=0`).

---

## C3 — Honor outbound idempotency (no duplicate DMs)

**Root cause:** `idempotency_key` is attached to outbound messages but `SlackChatAdapter.send_dm` (`slack.py:83`) ignores `message.metadata` and just posts text. Slack `chat.postMessage` has no native dedup, so a durable-step retry after `send_dm` succeeded but before `record_checkin_node` (`status_collector.py:757`) re-sends the DM.

**Fixes:**

1. **App-side send-once guard.** Before sending, `SlackChatAdapter.send_dm` checks a `sent_messages` store keyed by `(tenant_id, idempotency_key)` — Redis in container mode (a short TTL), in-memory for local/tests. If present, return the stored `ts` without re-posting; else post and record the `ts`. This makes send idempotent under retry and fixes the double-DM window at its source.
2. **Order-of-operations hardening.** In the daily-checkin graph, record the correlation intent (pending) **before** send where feasible, then send keyed by idempotency, then mark recorded — so a crash between send and record is recoverable without a second DM.
3. Reuse the same guard for nudges and cross-person notifications (all already set `idempotency_key`).

**Acceptance:** forcing a retry of the compose/send step (or replaying the workflow) results in exactly one Slack DM.

---

## C4 — Slack ↔ Jira ↔ VCS identity mapping

**Root cause:** `build_context` calls `IssueTracker.list_active_for(assignee)` with the developer's Slack user id, but Jira indexes by `accountId` (`jira_adapter.py:71` builds `assignee = <external_id>`). Result: no active issues found, so the "confirm your in-progress work" DM is generic — defeating the low-friction premise. Flagged blocked in `uat.md` as UAT-D1-003.

**Fixes:**

1. **Model identity links.** Add an `identity_links` concept keyed by `(tenant_id, developer_node_id)` holding `{ slack_user_id, jira_account_id, jira_email, gitlab_username, github_login }`. Store as developer-node attributes in the graph or a dedicated table behind a repository port (keep it provider-neutral in `core`).
2. **Resolve before querying.** In `build_context`, resolve the developer's `jira_account_id` (and VCS handle) from the mapping and pass that to `list_active_for` — not the Slack id.
3. **Auto-match on sync.** Directory sync can match by email (`users:read.email` scope, already documented in `slack.md`) against Jira user email / GitLab email to pre-populate mappings; leave a manual override.
4. **Admin surface.** Extend the config API + Admin UI (`MANAGE_CONFIG`) to view/set a member's identity links. Show "unmapped" members prominently since they get degraded check-ins.

**Acceptance:** for a mapped member, the check-in DM references their actual active Jira issues (e.g. "You have `OPDAY1-1` In Progress — done or still going?"); the SM/admin can see which members are unmapped.

---

## Lower-severity correctness items (fold into the same pass)

- **Locale/heuristic fragility.** `_is_trivial_non_status_reply` (fixed 8-word English set, `status_parsing.py:261`), `_explicitly_resolves_blockers` and `_looks_like_prompt_echo` (`status_collector.py:1604,1410`) are English-only and easy to fool ("no blockers" inside a longer sentence flips carry-forward off). Prefer the LLM classifier's structured decision over substring matching for the semantic calls; keep the pre-LLM shortcut list minimal and documented, or make it configurable per tenant/locale.
- **Magic values → settings.** `OUTBOUND_DM_MAX_CHARS = 320`, `RECENT_FACT_LOOKBACK_DAYS = 30`, history caps, 96-char subject truncation — lift into `config/settings.py`.
- **Cost accounting.** `TokenUsage.cost_usd` is hardwired to `0.0` (`litellm_provider.py:196`); capture LiteLLM's returned cost so Langfuse traces are meaningful (small, and it makes Plan 03/04 cost visibility real).

---

## Testing

- **Unit:** `local_date`/`resolve_timezone` across zones; `developers_without_checkin` for a dev whose local day ≠ UTC day; timezone validator (accept/reject); DST spring-forward and fall-back scheduling; `extract_json_object` on fenced/prose/corrupt inputs; safe-fallback (corrupt clarify → not sufficient); send-once guard under simulated retry; `build_context` uses `jira_account_id`.
- **Contract:** identity-link repository; `LiteLlmProvider` sends `response_format`/`temperature` (respx assertion).
- **Integration/BDD:** end-to-end check-in for a mapped developer shows active Jira issue in the DM; a fenced-JSON model reply finalizes correctly; a corrupt reply routes to clarification not green.

## Rollout

1. C2 (structured output) and C3 (idempotency) are low-risk and high-value — ship first; they also harden Plan 01's retry paths.
2. C1 requires a migration + backfill (`checkin_date`) — ship behind verification against real multi-tz data.
3. C4 (identity mapping) unblocks the core value loop and is the prerequisite for Plan 04's "contextual check-ins" — sequence it before Plan 03 write-back.

## Acceptance criteria (rollup)

- No status is ever finalized as healthy from an unparseable model reply.
- Check-in eligibility and scheduling are correct for developers in any timezone, across DST.
- Retrying an outbound step never produces a duplicate DM.
- A mapped developer's check-in references their real active Jira work.
