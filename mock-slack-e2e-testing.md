# Mock Slack E2E Testing Status

## Current Status

Status: `Complete - All Mock Slack E2E rows passed`

Attempt 9 remains the last fully verified Chrome-extension `/mock-slack` happy
path: dispatch created an outbound mock Slack DM, a reply was processed through
the simulator/webhook path, and Dharam's pod check-in status updated to
confirmed.

Attempts 17-20 expanded coverage to `MS-E2E-042`, rebuilt backend/worker with
the 15-minute check-in fanout override, and verified most of the
non-restartable matrix slice.

Attempt 21 found a real rapid duplicate reply race on the processed path.
Attempt 22 added the atomic final-reply persistence guard, rebuilt
backend/worker, and reran `MS-E2E-019` live. The final-state safety property
now passes: concurrent processed replies can both be recorded in simulator
state, but only one final check-in/status/fact persists and the loser does not
overwrite it. Attempt 23 verified non-admin simulator API access returns `403`.
Attempt 24 verified simulator-disabled routes return `404`. Attempt 26 fixed
compose environment pass-through and verified non-local simulator routes return
`404`. Attempt 27 verified the chat-provider mismatch case. Attempt 28 restored
the local admin Mock Slack stack and verified `MS-E2E-012`: admin navigation
exposes `/mock-slack`, non-admin role navigation hides Mock Slack/Admin Config,
and protected simulator APIs return `403` when the backend dev principal is
non-admin. All matrix rows are now complete.

## E2E Test Case Matrix

```csv
ID,Area,Priority,Scenario,Preconditions,Steps,Expected Result,Status,Evidence
MS-E2E-001,Preflight,P0,"Simulator enabled local admin preflight","Local backend; admin role; mock_slack chat; simulator enabled","GET /health; GET /ready; GET /test/chat-simulator/status; GET /test/chat-simulator/messages","Health/readiness pass; simulator routes return 200; status shows enabled=true provider=mock_slack","Passed Attempt 11","/health 200; /ready 200; status/messages 200; provider=mock_slack"
MS-E2E-002,Provider Config,P0,"Full local provider stack avoids real integrations","Backend and worker env use mock_slack chat/directory plus fake calendar/issue/VCS","Inspect backend and worker env; dispatch one check-in","Dispatch does not call real Slack/Jira/GitHub/Google; outbound DM appears","Passed Attempt 22","Backend/worker env used mock_slack chat/directory plus fake calendar/issue/VCS; U1003 dispatch created mock Slack bot 1782638093.000001 without real Slack/Jira/GitHub/Google"
MS-E2E-003,Schedule Config,P0,"Daily check-in fanout can be configured to 15 minutes","Compose stack started with PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *","Inspect backend and worker env; inspect worker schedule bootstrap logs","Both containers expose the override; check-in fanout schedule is configured with */15 * * * *","Passed Attempt 13","Schedule table has */15 * * * *; deterministic trigger and natural 05:45 UTC cron both succeeded after DBOS fanout fix"
MS-E2E-004,Directory,P0,"Mock directory sync exposes stable users","PULSEOPS_DIRECTORY_PROVIDER=mock_slack; admin role","POST /config/directory/sync; search users; add missing mock users","Mock users such as U1001/U1002/U1003 are available and can become configured members","Passed Attempt 11","Directory sync 200; total=3; U1002/Liam Chen added as mock_slack member"
MS-E2E-005,Happy Path,P0,"Dispatch and reply confirm member status","Eligible configured member; no blocking same-date stale schedule run; simulator empty","Open /mock-slack; select member; Send DM; select bot DM; Submit reply; check pod status","Bot DM recorded; reply status=processed; member check-in becomes confirmed with reply summary","Passed Attempt 12","UI dispatch/reply for Bharat created bot 1782624037.000001 and user 1782624062.000002; pod confirmed=2 missing=0"
MS-E2E-006,Simulator State,P1,"Refresh does not duplicate messages","At least one bot DM exists","Record message_count; click Refresh multiple times; GET messages","Messages reload and count remains unchanged","Passed Attempt 12","Two UI Refresh clicks left Messages KPI at 2 and timeline at one bot plus one user message"
MS-E2E-007,Simulator State,P0,"Reset clears simulator mailbox only","Messages exist; member/status data exists","Click Reset; confirm; GET messages; check members and pod check-ins","Messages empty and message_count=0; configured members and persisted check-ins remain","Passed Attempt 11","DELETE state 204; messages empty; members and persisted pod check-in data remained"
MS-E2E-008,Simulator State,P1,"Reply after reset rejects stale message ID","Have bot message ID; simulator reset","POST reply to old message ID","API returns 404; no user message is created","Passed Attempt 11","Reply to old 1782622890.000001 returned 404; mailbox stayed empty"
MS-E2E-009,Access Control,P0,"Non-admin cannot use simulator API","Simulator enabled local; caller lacks admin/MANAGE_CONFIG","GET status/messages; POST reply; DELETE state as non-admin","Simulator routes return 403","Passed Attempt 23","Backend restarted with PULSEOPS_DEV_PRINCIPAL_ROLES=dev; status/messages/reply/state all returned 403 with dev-user not authorized for manage_config"
MS-E2E-010,Access Control,P0,"Simulator disabled returns not found","Backend started with PULSEOPS_CHAT_SIMULATOR_ENABLED=false","GET /test/chat-simulator/status","Route returns 404","Passed Attempt 24","Backend restarted with simulator_enabled=false; status and messages returned 404 not found"
MS-E2E-011,Access Control,P0,"Non-local environment returns not found","Backend environment is not local; simulator flag true","GET /test/chat-simulator/status","Route returns 404","Passed Attempt 26","After compose env pass-through fix, backend/worker ran with PULSEOPS_ENVIRONMENT=staging; health reported staging; status/messages returned 404 not found"
MS-E2E-012,Access Control,P1,"Mock Slack navigation is admin/local gated","Frontend local/dev; compare admin and non-admin role views","Open app shell as admin and non-admin","Admin can access Mock Slack; non-admin cannot use protected simulator APIs","Passed Attempt 28","Admin role showed Admin Config and Mock Slack nav; /mock-slack loaded with local admin test surface, 3 messages, 6 members, and no console errors. Developer role hid Mock Slack/Admin Config nav; direct mounted route remains nav-gated caveat. Backend recreated with dev role returned 403 for status/messages/reply/state, then restored to admin with status 200"
MS-E2E-013,Provider Config,P0,"Chat provider mismatch prevents DM capture","Backend/worker not both using mock_slack","Dispatch check-in; inspect simulator and logs","No simulator DM appears; mismatch is detectable in env/logs","Passed Attempt 27","Backend/worker ran with chat_provider=fake and simulator_enabled=true; registry.chat_simulator_available=false; status/messages returned 404 before and after U1003 2026-08-13 dispatch, so no mock simulator DM was capturable"
MS-E2E-014,Provider Config,P1,"Invalid provider env fails fast","Unsupported provider value configured","Start backend or load settings","Settings validation rejects invalid provider","Passed Attempt 16","Settings rejected invalid chat/directory/calendar/issue/VCS provider values"
MS-E2E-015,Schedule Fanout,P1,"15-minute fanout dispatches missing eligible members","Two eligible configured members without same-date check-ins","Allow schedule to fire or trigger fanout deterministically; inspect workflows/messages","One check-in workflow is dispatched per missing developer","Passed Attempt 13","DBOS trigger returned dispatched=5 and SUCCESS; natural 05:45 UTC cron also recorded SUCCESS"
MS-E2E-016,Idempotency,P0,"Same member/date double dispatch does not create second DM","One successful sent schedule run exists for member/date","Dispatch same member/date again","Existing run short-circuits; no duplicate outbound DM","Passed Attempt 12","After reset, redispatch for same Bharat/date left simulator mailbox at 0 and reused one sent schedule run"
MS-E2E-017,Idempotency,P0,"Stale skipped_weekend run blocks rerun","Member preference changed to allow day but old skipped_weekend run remains","Dispatch same member/date","Workflow returns existing skipped run and sends no DM","Observed Attempt 13","Schedule fanout created skipped_weekend rows for Sunday-ineligible U1001/U1002/U0BAZ8DFLR1; these block same-date sends"
MS-E2E-018,Duplicate Reply,P0,"Duplicate direct reply to same bot message","One bot DM; first reply already processed","POST second reply to same bot message","Persisted status is not overwritten unexpectedly; duplicate handling is clear","Passed Attempt 12","Second UI reply was recorded as simulator message 1782624179.000003 but persisted summary stayed on the first confirmed reply"
MS-E2E-019,Duplicate Reply,P1,"Rapid double submit is safe","Bot DM selected; reply text entered","Double-click Submit reply or send two POSTs quickly","UI/API do not create conflicting final status updates","Passed with API caveat Attempt 22","Two concurrent replies to 1782638093.000001 both returned 200/processed and simulator recorded both user messages; final persistence had one checkin/status/fact containing Reply B, with Reply A counts 0 and no overwrite"
MS-E2E-020,Invalid IDs,P0,"Unknown simulator message ID is rejected","Simulator enabled","POST /test/chat-simulator/messages/not-real/reply","API returns 404; no user reply recorded","Passed Attempt 11","Reply to does-not-exist-qa-020 returned 404; mailbox stayed empty"
MS-E2E-021,Invalid IDs,P1,"Reply to a user message ID is rejected","A user reply exists","POST reply using a user message_id as parent","API rejects the reply or does not create nested user replies","Passed Attempt 14","Reply to user message 1782625251.000003 returned 404; no nested user reply was created"
MS-E2E-022,Invalid IDs,P1,"Unknown member dispatch fails safely","No configured member for developer_id","POST dispatch with unknown developer_id/chat ID","No orphan confirmed status is created; failure is clear","Passed Attempt 21","After rebuild, UQA_GUARD_1782636160 dispatch for 2026-07-09 returned 404; simulator message_count stayed 2; schedule_run/checkins/graph_nodes stayed 0"
MS-E2E-023,Invalid IDs,P1,"Bad chat_external_id does not corrupt member status","Valid member; nonmatching chat_external_id supplied","Dispatch; reply if a DM is recorded; inspect statuses","No cross-member status corruption occurs","Passed with caveat Attempt 21","U1003 2026-07-10 dispatched with chat_external_id=U1002 sent DM 1782636241.000001 to U1002; reply returned 200/ignored; U1003 raw_reply stayed null and no U1002 schedule row was created"
MS-E2E-024,Multi-Member,P0,"Replies correlate to the correct member","Two members each have bot DMs","Submit separate replies to each bot DM; inspect pod check-ins","Each member status updates only from its own reply","Passed Attempt 14","U1001/U1002 bot and user replies kept distinct channel/user/correlation IDs; raw replies persisted on matching correlations"
MS-E2E-025,Multi-Member,P1,"Newest bot message selection is intentional","Existing bot message selected; another dispatch occurs","Observe selected reply target before submit","Tester can clearly select intended DM; reply is not sent to an older wrong target","Passed with UX caveat Attempt 14","Bot selector showed both U1001 and U1002 targets clearly; selected value remained U1001 until changed"
MS-E2E-026,Reply Parsing,P1,"Short complete status parses without clarification","Bot DM exists; LLM path available","Submit concise progress/no-blockers status","Reply processes and summary preserves intent","Passed Attempt 12","Bharat no-blockers reply persisted exactly as submitted despite parser warning fallback"
MS-E2E-027,Reply Parsing,P1,"Blocker reply preserves blocker signal","Bot DM exists","Submit status with explicit blocker","Confirmed status includes blocker context in status or blocker view","Passed raw persistence Attempt 14","U1002 blocker reply persisted on its check-in correlation; downstream blocker view not yet separately verified"
MS-E2E-028,Reply Parsing,P2,"Ambiguous non-status reply does not become false healthy status","Bot DM exists","Submit vague text such as ok","System acknowledges or clarifies instead of falsely recording a healthy detailed status","Passed Attempt 19","Reply ok to 1782635523.000001 returned 200/ignored; user message recorded, but checkin stayed open with no raw_reply/replied_at and correlation unconsumed"
MS-E2E-029,Reply Parsing,P2,"Parser JSON failures fall back safely","LLM returns malformed/non-JSON output or warning path is hit","Submit normal reply","Warnings may log, but reply still persists with raw reply fallback","Passed Attempt 20","Live logs showed clarification/status parser JSON warnings for U1003 replies; raw replies still persisted and correlations were consumed"
MS-E2E-030,Reset Persistence,P1,"Reset after confirmed reply preserves app status","Member confirmed from mock reply","Reset simulator; refresh dashboard/check-ins","Simulator empty; member remains confirmed for persisted date","Passed Attempt 12","UI reset cleared mailbox to 0 while Bharat remained confirmed for 2026-06-28"
MS-E2E-031,UI Polling,P1,"Message polling surfaces delayed DM","Dispatch returns before message is visible","Wait at least two 5-second polling intervals","Timeline updates without full page reload","Passed after fix Attempt 15","Timeline and Messages KPI both updated from 4 to 5 on poll without manual Refresh"
MS-E2E-032,UI Refresh,P2,"Member list refresh behavior is known","Open /mock-slack; add member from admin in another tab","Click Refresh on Mock Slack page","Simulator/persona data refresh; any config-member dropdown staleness is documented","Observed Attempt 20","After adding U1003, API members=6 but Mock Slack Refresh left Members KPI at 5; full route reload showed 6"
MS-E2E-033,UI Validation,P1,"Blank or whitespace-only reply cannot be submitted","/mock-slack loaded; at least one bot DM exists","Select a bot DM; enter only spaces/newlines in Message; observe Submit reply; inspect messages count","Submit reply remains disabled; no reply API call is sent; simulator message count is unchanged","Passed Attempt 19","Whitespace-only textarea kept Submit reply disabled; UI KPI and API message_count stayed 7"
MS-E2E-034,API Validation,P1,"Empty reply payload is rejected without side effects","Simulator enabled; bot DM exists","Record message_count; POST /test/chat-simulator/messages/{bot_id}/reply with text empty string","API returns 422; no user message is recorded; app check-in status is unchanged","Passed Attempt 18","Empty text reply to 1782625563.000005 returned 422 string_too_short; message_count stayed 7; checkin/correlation stayed open"
MS-E2E-035,API Validation,P1,"Invalid reply received_at is rejected without side effects","Simulator enabled; bot DM exists","Record message_count; POST reply with valid text and invalid received_at value","API returns 422; no user message is recorded; app check-in status is unchanged","Passed Attempt 18","Invalid received_at reply returned 422 datetime parsing error; message_count stayed 7; checkin/correlation stayed open"
MS-E2E-036,UI State,P1,"Reset clears selected reply target and prevents stale UI reply","Bot DM selected in /mock-slack; reply text present","Click Reset; confirm dialog; inspect reply selector and Submit reply state","Timeline is empty; selected bot target is cleared; Submit reply is disabled until a new bot message exists","Passed Attempt 19","UI Reset changed Messages KPI to 0, timeline to No messages, selector to Select message only, and Submit reply stayed disabled"
MS-E2E-037,UI State,P1,"New bot message auto-selects only when no reply target is selected","/mock-slack loaded with no selected bot target","Create or dispatch a new bot DM; wait for polling or click Refresh","Newest bot DM becomes selected once; existing intentional selections are not overwritten","Passed Attempt 19","After reset, external dispatch created 1782635523.000001; polling moved KPI to 1 and auto-selected U1001 - status_checkin"
MS-E2E-038,Webhook Path,P1,"Slack-shaped webhook reply processes without test-support reply endpoint","Recorded bot DM with correlation_id exists","POST Slack-shaped event payload to /webhooks/chat/mock_slack using bot channel/user and reply text","Webhook returns processed; matching check-in is updated; simulator state remains internally consistent","Passed Attempt 20","Direct Slack-shaped webhook for U1003 returned processed; raw reply persisted, correlation consumed, simulator mailbox stayed at one bot message"
MS-E2E-039,Message Contract,P1,"Simulator message payload contract is complete for bot and user messages","At least one bot DM and one user reply exist","GET /test/chat-simulator/messages; inspect bot and user records","Bot records include purpose/status metadata and correlation_id; user records include reply_to_message_id, source metadata, same channel/user/correlation","Passed Attempt 18","All 7 messages had required fields; 5 bot records had purpose=status_checkin/correlation metadata; 2 user replies had reply_to_message_id and source=mock_slack"
MS-E2E-040,UI Timeline,P2,"Timeline groups multiple Slack users with counts matching API","Simulator has messages for at least two users","Open /mock-slack; compare grouped UI sections and badges with GET messages grouped by user_id","Each user has one group; group counts match API items; timeline remains scrollable/readable","Passed Attempt 19","Before reset, UI groups matched API counts: U1001=3, U1002=2, UQA_UNKNOWN=1, UQA_UNKNOWN_LIVE=1"
MS-E2E-041,Simulator State,P1,"Post-reset future-date dispatch reopens mock channel cleanly","Simulator reset is acceptable; member has no schedule run for chosen future date","Reset simulator; dispatch configured member for unused future date; GET messages","A new bot DM appears with deterministic D-channel for the chat user; no channel-not-open error occurs","Passed Attempt 19","After reset, U1001 2026-07-03 dispatch returned 200 and created bot 1782635523.000001 on D1001 with message_count=1"
MS-E2E-042,Reply Parsing,P2,"Long multiline status reply preserves raw formatting","Bot DM exists; reply text includes multiple lines with progress, blockers, and ETA","Submit multiline reply; inspect simulator timeline and persisted check-in raw_reply","Reply processes; line breaks/content remain readable in timeline and persisted raw reply preserves intent","Passed Attempt 20","Multiline reply to 1782635927.000001 returned processed; simulator user text and persisted raw_reply preserved line breaks"
```

## Iteration Log

### Attempt 28 - 2026-06-28 Mock Slack Navigation And Final Access Gate

Action:

- Restored the live backend and worker from the provider-mismatch state to the
  local admin Mock Slack E2E stack:
  `PULSEOPS_ENVIRONMENT=local`,
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  fake calendar/issue/VCS providers,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- Recreated only backend and worker without rebuilding, preserving Docker
  volumes and leaving orphan containers untouched.
- Executed `MS-E2E-012` through the app shell and simulator API gates.
- Temporarily recreated only the backend with
  `PULSEOPS_DEV_PRINCIPAL_ROLES=dev` to verify non-admin protected API
  behavior, then restored it to admin.

Expected result:

- Admin/local Mock Slack configuration exposes simulator APIs.
- Admin role can see and open Mock Slack navigation.
- Non-admin role cannot navigate to Mock Slack through the app shell and cannot
  use protected simulator APIs.
- Final stack is restored to the admin Mock Slack E2E state.

Observed result:

- Admin/local preflight passed:
  - Backend and worker env matched the local admin Mock Slack stack, including
    fake calendar/issue/VCS providers and
    `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
  - `/health` returned `200` with `environment=local`.
  - `/ready` returned `200` with all dependencies true.
  - `/test/chat-simulator/status` returned `200` with `enabled=true`,
    `provider=mock_slack`, and `message_count=3`.
  - `/test/chat-simulator/messages` returned `200` with three simulator
    messages.
- `MS-E2E-012` passed:
  - Frontend `http://127.0.0.1:5173/` loaded and redirected to `/me`.
  - With Active role `Admin`, the primary nav showed `Admin Config` and
    `Mock Slack`.
  - `/mock-slack` loaded with the `Mock Slack` heading, the
    `Local admin test surface` label, Messages `3`, Members `6`, and no
    browser console errors.
  - After switching Active role to `Developer`, the UI showed
    `Viewing as Developer`, and both `Mock Slack` and `Admin Config` nav links
    were hidden.
  - Caveat: if `/mock-slack` is already mounted, switching the local UI role to
    Developer does not unmount the direct route; the frontend currently gates
    navigation visibility rather than the route itself.
  - With backend temporarily running as non-admin
    `PULSEOPS_DEV_PRINCIPAL_ROLES=dev`, simulator routes returned `403`:
    status, messages, reply, and reset all returned
    `dev-user is not authorized for manage_config`.
- Final restore passed:
  - Backend was recreated back to the admin Mock Slack env.
  - `/test/chat-simulator/status` returned `200`.
  - Frontend `http://127.0.0.1:5173/` returned `200`.
  - Backend and worker were up.

Final status:

- All Mock Slack E2E matrix rows have passed or have documented observed
  behavior/caveats.

### Attempt 17 - 2026-06-28 Live MS-E2E-022 Retest And Matrix Expansion

Action:

- Added new CSV-style E2E scenarios `MS-E2E-033` through `MS-E2E-042`.
- Rebuilt and recreated only backend and worker so the live stack picked up the
  current unknown-developer dispatch fix.
- Preserved Docker volumes and unrelated services.
- Kept the Mock Slack quick-test runtime env:
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CALENDAR_PROVIDER=fake`,
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`,
  `PULSEOPS_VCS_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.

Expected result:

- Backend and worker expose the 15-minute fanout and mock/fake provider env.
- Live backend code includes the configured-developer guard in the admin
  dispatch route.
- Unknown developer dispatch returns `404` without schedule-run, check-in,
  correlation, or simulator-message side effects.

Observed result:

- Rebuild/recreate completed for backend and worker with:
  `docker compose up -d --build --force-recreate --no-deps backend worker`.
- Backend and worker env verification passed for the mock/fake providers and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- Live preflight passed:
  - `/health` returned `200`.
  - `/ready` returned `200`.
  - `/test/chat-simulator/status` returned `200` with `enabled=true`,
    `provider=mock_slack`, and `message_count=7`.
  - `/test/chat-simulator/messages` returned `200`.
- Backend container code check found:
  - `get_config_service` import.
  - `config_service` dependency on the check-in dispatch route.
  - `_ensure_configured_developer(...)` before workflow dispatch.
- `MS-E2E-022` passed live:
  - Fresh unknown developer/date:
    `developer_id=UQA_UNKNOWN_FIXED_17`, `checkin_date=2026-07-02`.
  - Before dispatch:
    `message_count=7`, `checkin_schedule_runs=0`, `checkins=0`, and
    `checkin_correlations=0`.
  - Dispatch returned `404` with:
    `developer UQA_UNKNOWN_FIXED_17 not found for tenant demo`.
  - After dispatch:
    `message_count=7`, `checkin_schedule_runs=0`, `checkins=0`, and
    `checkin_correlations=0`.
- Docker reported the existing orphan-container warning; no orphan containers
  were removed.

Next step:

- Continue the non-disruptive pending cases first:
  `MS-E2E-019`, `MS-E2E-028`, `MS-E2E-032`, and the new validation/state
  rows. Leave restart-only access-control/provider-mismatch cases for a
  deliberate restart pass.

### Attempt 18 - 2026-06-28 API Validation And Message Contract Pass

Action:

- Execute non-disruptive API validation cases against the rebuilt 15-minute
  Mock Slack E2E stack.
- Use existing bot message `1782625563.000005` for `U1001` on `2026-06-30`,
  whose check-in/correlation was still open before the test.
- Inspect simulator message contract using current message data.

Expected result:

- Empty reply text and invalid `received_at` are rejected with `422`.
- The simulator mailbox count does not change.
- The target check-in remains unanswered and its correlation remains
  unconsumed.
- Existing bot and user simulator messages expose the expected metadata fields.

Observed result:

- Before validation:
  - `/test/chat-simulator/status` returned `message_count=7`.
  - Target check-in check returned one open row with `raw_reply is null` and
    `replied_at is null`.
  - Target correlation check returned one unconsumed row.
- `MS-E2E-034` passed:
  - Posting `{ "text": "", "received_at": "2026-07-02T09:35:00Z" }`
    to `/test/chat-simulator/messages/1782625563.000005/reply` returned
    `422` with `string_too_short`.
- `MS-E2E-035` passed:
  - Posting a valid text with `"received_at": "not-a-date"` returned `422`
    with a datetime parsing error.
- After validation:
  - `/test/chat-simulator/status` still returned `message_count=7`.
  - Target check-in still had no `raw_reply` and no `replied_at`.
  - Target correlation remained unconsumed.
- `MS-E2E-039` passed:
  - All 7 simulator messages included the required response fields.
  - 5 bot messages had `purpose=status_checkin`, a correlation ID, and
    `metadata.purpose=status_checkin`.
  - 2 user messages had `reply_to_message_id`, a correlation ID, and
    `metadata.source=mock_slack`.

Next step:

- Execute browser-visible UI rows, especially `MS-E2E-033`, `MS-E2E-036`,
  `MS-E2E-037`, and `MS-E2E-040`, then continue controlled reply-processing
  cases on unused future-date check-ins.

### Attempt 19 - 2026-06-28 UI State And Ambiguous/Rapid Reply Pass

Action:

- Use the Chrome-extension Playwright session against
  `http://127.0.0.1:5173/mock-slack`.
- Verify UI validation, grouping, reset state, polling auto-selection, and a
  post-reset future-date dispatch.
- Execute controlled ambiguous and rapid duplicate reply probes through the
  simulator API.

Expected result:

- Whitespace-only UI replies cannot be submitted.
- UI timeline grouping matches simulator API message counts.
- Reset clears simulator messages and selected reply state without touching
  configured members.
- A new bot DM created after reset is visible through polling and becomes the
  selected reply target when no previous target is selected.
- Ambiguous replies do not become false healthy status.
- Rapid duplicate submits do not create conflicting final status updates.

Observed result:

- Chrome-extension `/mock-slack` opened as Admin, with Mock Slack visible in
  navigation and no browser warnings/errors during the pass.
- `MS-E2E-033` passed:
  - Filled the reply textarea with whitespace/newline/tab content.
  - `Submit reply` remained disabled.
  - UI KPI and API status both stayed at `message_count=7`.
- `MS-E2E-040` passed before reset:
  - API grouping was `U1001=3`, `U1002=2`, `UQA_UNKNOWN=1`, and
    `UQA_UNKNOWN_LIVE=1`.
  - UI group badges showed the same counts and timeline remained readable.
- `MS-E2E-036` passed:
  - UI Reset confirmed through the dialog.
  - Messages KPI became `0`.
  - Timeline showed `No messages`.
  - Bot-message selector had only `Select message`.
  - `Submit reply` remained disabled.
- `MS-E2E-041` and `MS-E2E-037` passed:
  - Precheck found no `U1001` schedule run or check-in for `2026-07-03`.
  - API dispatch for `U1001` / `2026-07-03` returned `200` with workflow
    `checkin-demo-U1001-2026-07-03-dde6b536-51fd-4f77-8985-664cd7543599`.
  - Simulator recorded one bot DM:
    `1782635523.000001`, `user_id=U1001`, `channel_id=D1001`,
    `purpose=status_checkin`.
  - The UI polling loop updated the Messages KPI to `1`, showed the U1001
    timeline group, and auto-selected `U1001 - status_checkin`.
- `MS-E2E-028` passed:
  - Replying `ok` to bot `1782635523.000001` returned `200` with
    `status=ignored`.
  - A simulator user message was recorded.
  - The target check-in stayed open: no `raw_reply`, no `replied_at`, and the
    correlation stayed unconsumed.
- `MS-E2E-019` passed with caveat:
  - Precheck found no `U1002` schedule run or check-in for `2026-07-05`.
  - API dispatch created bot `1782635655.000001`.
  - Two concurrent replies to the same bot message both returned `200` with
    `status=ignored`.
  - Simulator recorded both user messages, but the check-in stayed open and no
    conflicting final status update was created.
  - Caveat: this proved duplicate-submission safety, but not duplicate
    handling after a successful final status because the runtime classified
    both rapid replies as ignored.

Next step:

- Continue pending non-restart cases where possible:
  `MS-E2E-023`, `MS-E2E-029`, `MS-E2E-038`, and `MS-E2E-042`.
  Keep `MS-E2E-010`, `MS-E2E-011`, and `MS-E2E-013` for an explicit
  restart/env-negative pass.

### Attempt 20 - 2026-06-28 Remaining Non-Restart Cases

Action:

- Execute remaining non-restartable cases:
  `MS-E2E-032`, `MS-E2E-038`, `MS-E2E-042`, `MS-E2E-023`, and
  `MS-E2E-029`.
- Promote the last mock directory user `U1003` / Mina Patel through the
  supported admin API.
- Use fresh future dates to avoid previous schedule-run/check-in state.

Expected result:

- Mock Slack page refresh behavior is documented when config members change.
- Direct Slack-shaped webhook replies work without the simulator reply endpoint.
- Multiline replies preserve raw formatting.
- Mismatched chat IDs do not corrupt another member's status.
- Parser JSON warnings do not prevent raw reply persistence.

Observed result:

- `MS-E2E-032` observed:
  - Before adding Mina, `/mock-slack` showed Members `5`.
  - `POST /config/members/from-directory` with `U1003` returned `201`.
  - `/config/members` then returned 6 members, including `U1003`.
  - Clicking `/mock-slack` Refresh left the Members KPI at `5`.
  - A full route reload showed Members `6`.
  - This documents that page Refresh does not currently invalidate the
    config-member query.
- `MS-E2E-038` passed:
  - Precheck found no `U1003` schedule run or check-in for `2026-07-06`.
  - Dispatch returned `200` with workflow
    `checkin-demo-U1003-2026-07-06-28a9db97-6e0e-4083-943b-698ad68bf17c`.
  - Simulator bot message:
    `1782635894.000001`, `user_id=U1003`, `channel_id=D1003`.
  - Direct `POST /webhooks/chat/mock_slack` with Slack-shaped event
    `ts=1783244100.038001` returned `200` and `status=processed`.
  - The check-in raw reply persisted as:
    `Mina finished product acceptance notes and is preparing stakeholder comms. No blockers.`
  - Correlation was consumed.
  - Simulator mailbox stayed internally consistent with one bot message,
    because this path bypassed the simulator reply injector.
- `MS-E2E-042` passed:
  - Precheck found no `U1003` schedule run or check-in for `2026-07-07`.
  - Dispatch returned `200` with workflow
    `checkin-demo-U1003-2026-07-07-8c60e83e-1254-472b-b928-fc40ce2d30b7`.
  - Reply to bot `1782635927.000001` returned `200` with
    `status=processed`.
  - Simulator user message `1782635928.000002` preserved line breaks.
  - Persisted `raw_reply` preserved:
    `Yesterday: completed QA matrix updates.`
    `Today: validating multiline reply persistence.`
    `Blockers: none.`
    `ETA: on track for handoff.`
- `MS-E2E-029` passed:
  - Live logs during U1003 webhook/reply processing showed
    `clarification_evaluator_json_decode_failed` and
    `status_parser_json_decode_failed`.
  - Despite those warnings, both U1003 raw replies persisted and their
    correlations were consumed on processed paths.
- `MS-E2E-023` passed with caveat:
  - Precheck found no `U1003` schedule run or check-in for `2026-07-08`.
  - Dispatching `developer_id=U1003` with `chat_external_id=U1002` returned
    `200` and created bot `1782636018.000001` for `user_id=U1002`,
    `channel_id=D1002`, with a U1003 correlation.
  - Reply to that bot returned `200` with `status=ignored`.
  - Simulator recorded the user reply, but the U1003 check-in had no
    `raw_reply`, no `replied_at`, and no signals.
  - No `developer_statuses` rows were created for either `U1002` or `U1003`
    on `2026-07-08`.
  - Correlation remained unconsumed with `chat_user_ref=U1002`.
  - Caveat: the system still sent the outbound DM to the mismatched chat user;
    the safety property verified here is that the reply did not corrupt member
    status.

Next step:

- Remaining rows require environment changes/restarts:
  `MS-E2E-009`, `MS-E2E-010`, `MS-E2E-011`, `MS-E2E-012`, and
  `MS-E2E-013`.

### Attempt 21 - 2026-06-28 Live Retest And Rapid Duplicate Race

Action:

- Re-ran live API checks after another backend/worker rebuild with the
  15-minute Mock Slack quick-test stack:
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CALENDAR_PROVIDER=fake`,
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`,
  `PULSEOPS_VCS_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- Verified the running backend image includes the unknown-developer guard.
- Retested `MS-E2E-022`, `MS-E2E-023`, and `MS-E2E-019` with fresh future
  dates and direct DB/API evidence.
- Spawned a sequential debugging subagent for the rapid duplicate failure.

Expected result:

- Unknown developer dispatch returns `404` without creating graph, schedule,
  check-in, correlation, or simulator-message side effects.
- Mismatched `chat_external_id` replies do not corrupt either the intended
  member or the mismatched chat user's status.
- Rapid double submit does not create conflicting final status updates and does
  not overwrite the first accepted reply.

Observed result:

- Focused regression checks passed before rebuild:
  - `PYTHONPATH=backend uv run pytest backend/tests/unit/test_api.py::test_admin_workflow_dispatch_routes_are_admin_only backend/tests/unit/test_api.py::test_admin_checkin_dispatch_rejects_unknown_developer_without_dispatch backend/tests/unit/test_agent_and_workflow.py::test_checkin_fanout_dispatches_developers_without_checkin backend/tests/unit/test_agent_and_workflow.py::test_dbos_dispatch_keeps_runtime_alive_for_started_workflows backend/tests/unit/test_agent_and_workflow.py::test_dbos_checkin_fanout_starts_children_from_workflow_context backend/tests/unit/test_testcontainers_compose.py --no-cov -q`
    completed with `7 passed`.
  - `uv run ruff check ...` and `uv run ruff format --check ...` both passed
    for the focused backend files.
- Backend and worker rebuild/recreate succeeded with
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
  - Existing orphan-container warning remained; orphan containers were left
    untouched.
- Rebuilt backend code check passed:
  - `/app/backend/api/routers/admin.py` contains `get_config_service`.
  - `/app/backend/api/routers/admin.py` contains
    `_ensure_configured_developer`.
- Runtime env check passed on both backend and worker for:
  - `PULSEOPS_CHAT_PROVIDER=mock_slack`
  - `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`
  - `PULSEOPS_CALENDAR_PROVIDER=fake`
  - `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`
  - `PULSEOPS_VCS_PROVIDER=fake`
  - `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`
  - `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`
  - `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`
- Live preflight passed:
  - `/health` returned `200`.
  - `/ready` returned `200`.
  - `/test/chat-simulator/status` returned `200` with `enabled=true`,
    `tenant_id=demo`, `provider=mock_slack`, and `message_count=2`.
  - `/test/chat-simulator/messages` returned `200`.
- `MS-E2E-022` passed live:
  - Fresh unknown developer/date:
    `developer_id=UQA_GUARD_1782636160`, `checkin_date=2026-07-09`.
  - Before dispatch, simulator `message_count=2`.
  - Dispatch returned `404` with:
    `developer UQA_GUARD_1782636160 not found for tenant demo`.
  - After dispatch, simulator `message_count=2`.
  - Database checks returned:
    `checkin_schedule_runs=0`, `checkins=0`, and `graph_nodes=0`.
- `MS-E2E-023` passed with caveat:
  - Simulator reset returned `204`.
  - `U1003` / Mina Patel was made every-day eligible through the supported
    check-in preference API.
  - Dispatching `developer_id=U1003`, `chat_external_id=U1002`,
    and `checkin_date=2026-07-10` returned `200` with workflow
    `checkin-demo-U1003-2026-07-10-ab95de06-c7ad-4827-b4d3-36369889c799`.
  - Simulator recorded bot message `1782636241.000001` with:
    `channel_id=D1002`, `user_id=U1002`, and a U1003 correlation.
  - Replying to that bot returned `200` with `status=ignored`.
  - The U1003 check-in row remained open with `raw_reply is null` and
    `replied_at is null`.
  - Only the U1003 schedule row existed for `2026-07-10`; no U1002 schedule
    row was created for that date.
  - Caveat remains: the workflow can send a DM to a mismatched chat user; the
    verified safety property is that the reply did not corrupt member status.
- `MS-E2E-019` failed:
  - Simulator reset returned `204`.
  - `U1003` was dispatched for `2026-07-11` and created bot message
    `1782636305.000001`.
  - Two concurrent reply POSTs were fired against the same bot message:
    - Reply A:
      `Rapid reply A: completed runbook validation, no blockers.`
    - Reply B:
      `Rapid reply B: duplicate submit should not overwrite final status.`
  - Both replies returned `200` with `status=processed`.
  - Simulator recorded two user messages:
    `1782636600.000002` and `1782636600.000003`.
  - Persisted `checkins.raw_reply` for the U1003/2026-07-11 correlation was
    overwritten by Reply B.
- Debugging subagent root-cause summary:
  - Duplicate handling is currently read-before-write.
  - Two concurrent requests can both observe `checkins.replied_at is null`.
  - The final write uses unconditional `record_checkin(...)`, so the later
    request can overwrite `raw_reply`.
  - Recommended fix is a repository-level first-writer-wins compare-and-set,
    using an atomic Postgres `UPDATE ... WHERE replied_at IS NULL`.

Next step:

- Implement the first-reply-wins persistence guard, add focused tests, rebuild
  backend/worker, and rerun `MS-E2E-019` until the matrix row passes.

### Attempt 22 - 2026-06-28 Rapid Duplicate Reply Guard Retest

Action:

- Added an atomic final-reply persistence guard at the status repository
  boundary.
- Rebuilt and force-recreated only backend and worker with the Mock Slack E2E
  env:
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CALENDAR_PROVIDER=fake`,
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`,
  `PULSEOPS_VCS_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- Preserved Docker volumes and left existing orphan containers untouched.
- Reran `MS-E2E-019` manually against the rebuilt live stack with a fresh U1003
  future-date check-in.

Expected result:

- Concurrent processed replies to one bot message must not create conflicting
  final application state.
- Exactly one final check-in/status/fact should persist for the correlation.
- The losing concurrent reply must not overwrite the winning final raw reply.

Observed result:

- Focused backend verification passed before rebuild:
  - `PYTHONPATH=backend uv run pytest backend/tests/unit/test_status_collector.py::test_status_collector_records_only_first_rapid_final_reply backend/tests/unit/test_status_collector.py::test_status_collector_handles_reply_by_correlation backend/tests/contract/test_ports.py::test_fake_status_repository_satisfies_contract backend/tests/unit/test_status_persistence.py::test_in_memory_store_satisfies_phase_1_repository_contracts --no-cov -q`
    completed with `4 passed`.
  - Broader sequential testing agent verification passed:
    `backend/tests/unit/test_status_collector.py`,
    `backend/tests/contract/test_ports.py`, and
    `backend/tests/unit/test_status_persistence.py` completed with
    `34 passed`.
    `backend/tests/unit/test_chat_simulator_api.py`,
    `backend/tests/unit/test_registry_providers.py`, and the targeted DBOS
    dispatch lifecycle test completed with `13 passed, 1 warning`.
  - `uv run ruff check ...` and `uv run ruff format --check ...` passed for
    the touched backend files.
- Rebuild/recreate completed for backend and worker with:
  `docker compose up -d --build --force-recreate --no-deps backend worker`.
- Backend and worker env checks matched the expected mock/fake provider stack
  and 15-minute fanout override.
- Live preflight passed:
  - `/health` returned `200` with `status=ok` and `tenant_id=demo`.
  - `/ready` returned `200` with all dependencies true.
  - `/test/chat-simulator/status` returned `200` with `enabled=true`,
    `provider=mock_slack`, and `tenant_id=demo`.
  - `/test/chat-simulator/messages` returned `200`.
- `MS-E2E-019` passed with API caveat:
  - Simulator reset returned `204`; post-reset `message_count=0`.
  - Fresh test date: `2026-07-12`.
  - Precheck for U1003 on that date found schedule/checkins/correlations,
    developer statuses, and facts all at `0`.
  - U1003 eligibility was set to every day through the supported check-in
    preference API.
  - Dispatch returned workflow
    `checkin-demo-U1003-2026-07-12-7f53a4c0-987f-475d-a6ed-a1bc862929ff`.
  - Simulator recorded bot message `1782638093.000001` on
    `channel_id=D1003`, `user_id=U1003`, with correlation
    `checkin-checkin-demo-U1003-2026-07-12-7f53a4c0-987f-475d-a6ed-a1bc862929ff`.
  - Two concurrent reply POSTs were fired against the same bot message:
    - Reply A:
      `Reply A: Yesterday I completed the MS-E2E-019 rebuild validation. Today I am checking that a second submit cannot overwrite U1003 status. No blockers.`
      returned `200`, `status=processed`, and simulator message
      `1782638105.000002`.
    - Reply B:
      `Reply B: Yesterday I completed the MS-E2E-019 rebuild validation. Today I am checking that a second submit cannot overwrite U1003 status. No blockers.`
      returned `200`, `status=processed`, and simulator message
      `1782638105.000003`.
  - Final simulator state had `3` messages: one bot plus both user replies.
  - Final database state had:
    - `checkins=1` row for the correlation.
    - `checkin_correlations=1` row with
      `consumed_at=2026-06-28 09:15:05.976+00`.
    - `developer_statuses=1` U1003 confirmed row for `as_of=2026-06-28`.
    - `facts=1` row for the correlation.
  - Final `checkins.raw_reply`, developer status summary, and fact payload all
    contained Reply B.
  - Counts showed `checkin_reply_a=0`, `checkin_reply_b=1`,
    `fact_reply_a=0`, and `fact_reply_b=1`.
  - Caveat: both API calls still returned `status=processed`; the loser is not
    surfaced as `duplicate` in the reply API response.

Next step:

- Execute the remaining restart/env-negative cases:
  `MS-E2E-009`, `MS-E2E-010`, `MS-E2E-011`, `MS-E2E-012`, and
  `MS-E2E-013`.

### Attempt 23 - 2026-06-28 Non-Admin Simulator API Gate

Action:

- Recreated only the backend with simulator enabled, local environment, mock
  Slack chat/directory providers, fake read providers, and
  `PULSEOPS_DEV_PRINCIPAL_ROLES=dev`.
- Preserved Docker volumes and did not remove orphan containers.

Expected result:

- A non-admin dev principal lacks `MANAGE_CONFIG`.
- Simulator status/messages/reply/reset routes return `403`.

Observed result:

- Backend env confirmed `PULSEOPS_DEV_PRINCIPAL_ROLES=dev`.
- `/health` returned `200 OK`.
- `MS-E2E-009` passed:
  - `GET /test/chat-simulator/status` returned `403`.
  - `GET /test/chat-simulator/messages` returned `403`.
  - `POST /test/chat-simulator/messages/not-real/reply` returned `403`.
  - `DELETE /test/chat-simulator/state` returned `403`.
  - All four responses returned:
    `{"detail":"dev-user is not authorized for manage_config"}`.

Next step:

- Continue restart/env-negative cases: `MS-E2E-010`, `MS-E2E-011`,
  `MS-E2E-012`, and `MS-E2E-013`.

### Attempt 24 - 2026-06-28 Simulator Disabled Gate

Action:

- Recreated only the backend with local environment, mock Slack chat/directory
  providers, fake read providers, admin dev roles, and
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=false`.
- Preserved Docker volumes and did not remove orphan containers.

Expected result:

- Simulator routes are hidden when the simulator flag is disabled.
- Status/messages return `404`.

Observed result:

- Backend env confirmed:
  - `PULSEOPS_CHAT_PROVIDER=mock_slack`
  - `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`
  - `PULSEOPS_CALENDAR_PROVIDER=fake`
  - `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`
  - `PULSEOPS_VCS_PROVIDER=fake`
  - `PULSEOPS_CHAT_SIMULATOR_ENABLED=false`
  - `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`
  - `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`
- `/health` returned `200` with `environment=local` and `tenant_id=demo`.
- `MS-E2E-010` passed:
  - `GET /test/chat-simulator/status` returned `404` with
    `{"detail":"not found"}`.
  - `GET /test/chat-simulator/messages` returned `404` with
    `{"detail":"not found"}`.

Next step:

- Continue restart/env-negative cases: `MS-E2E-011`, `MS-E2E-012`, and
  `MS-E2E-013`.

### Attempt 25 - 2026-06-28 Non-Local Gate Exposed Compose Gap

Action:

- Recreated only the backend with the intended non-local env:
  `PULSEOPS_ENVIRONMENT=staging`,
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  fake read providers, simulator enabled, and admin dev roles.
- Preserved Docker volumes and did not remove orphan containers.

Expected result:

- Backend health reports `environment=staging`.
- Simulator routes return `404` outside local environment.

Observed result:

- `MS-E2E-011` failed the setup precondition:
  - `/health` returned `200`, but still reported `environment=local`.
  - `docker compose exec -T backend printenv PULSEOPS_ENVIRONMENT` returned
    `local`.
  - `GET /test/chat-simulator/status` returned `200`.
  - `GET /test/chat-simulator/messages` returned `200`.
- Root cause:
  - `docker-compose.yml` did not pass through `PULSEOPS_ENVIRONMENT` for
    backend/worker, so the `.env` local value remained active.
- Fix applied:
  - Added `PULSEOPS_ENVIRONMENT: ${PULSEOPS_ENVIRONMENT:-local}` to backend
    and worker compose env.
  - Added focused compose regression coverage for the runtime environment
    override.
  - `PYTHONPATH=backend uv run pytest backend/tests/unit/test_testcontainers_compose.py --no-cov -q`
    completed with `2 passed`.
  - `uv run ruff check backend/tests/unit/test_testcontainers_compose.py`
    and `uv run ruff format --check backend/tests/unit/test_testcontainers_compose.py`
    passed.

Next step:

- Rebuild/recreate backend and worker with the compose pass-through fix, then
  rerun `MS-E2E-011`.

### Attempt 26 - 2026-06-28 Non-Local Gate Retest

Action:

- Rebuilt and recreated only backend and worker after adding compose
  `PULSEOPS_ENVIRONMENT` pass-through.
- Used:
  `PULSEOPS_ENVIRONMENT=staging`,
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  fake read providers, simulator enabled, admin dev roles, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- Preserved Docker volumes and did not remove orphan containers.

Expected result:

- Backend and worker receive `PULSEOPS_ENVIRONMENT=staging`.
- Health reports staging.
- Simulator routes return `404` outside local environment.

Observed result:

- Backend and worker env both showed `PULSEOPS_ENVIRONMENT=staging`.
- `/health` returned `200` and included `"environment":"staging"`.
- `MS-E2E-011` passed:
  - `GET /test/chat-simulator/status` returned `404` with
    `{"detail":"not found"}`.
  - `GET /test/chat-simulator/messages` returned `404` with
    `{"detail":"not found"}`.

Next step:

- Continue remaining rows: `MS-E2E-012` and `MS-E2E-013`.

### Attempt 27 - 2026-06-28 Provider Mismatch Negative

Action:

- Rebuilt and recreated only backend and worker with local environment,
  `PULSEOPS_CHAT_PROVIDER=fake`, mock Slack directory, fake read providers,
  simulator flag enabled, admin dev roles, and the 15-minute fanout override.
- Preserved Docker volumes and did not remove orphan containers.
- Verified simulator availability before and after a fresh U1003 dispatch.

Expected result:

- Provider mismatch is detectable in env/runtime state.
- Simulator routes are unavailable because the active chat provider is not
  `mock_slack`.
- Dispatch does not create a capturable mock Slack simulator DM.

Observed result:

- Backend and worker env both showed:
  - `PULSEOPS_CHAT_PROVIDER=fake`
  - `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`
  - `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`
  - fake calendar/issue/VCS providers
- Runtime checks in both containers showed:
  - `settings.chat_provider=fake`
  - `settings.chat_simulator_enabled=True`
  - `registry.chat_simulator_available=False`
- `/health` returned `200 OK` with `environment=local` and `tenant_id=demo`.
- `MS-E2E-013` passed:
  - Before dispatch, `GET /test/chat-simulator/status` returned `404` with
    `{"detail":"not found"}`.
  - Before dispatch, `GET /test/chat-simulator/messages` returned `404` with
    `{"detail":"not found"}`.
  - Fresh dispatch for `developer_id=U1003`, `chat_external_id=U1003`,
    `checkin_date=2026-08-13` returned `200 OK` with workflow
    `checkin-demo-U1003-2026-08-13-b6f0c027-d550-4cbf-b7e3-b628f80ea946`.
  - After dispatch, simulator `/status` and `/messages` still returned `404`,
    so no mock simulator DM was capturable.
  - Logs showed the dispatch `200 OK` and subsequent simulator route
    `404 Not Found` responses, making the mismatch visible with env/runtime
    evidence.

Historical pause / resume:

- Testing paused by user request at the end of Attempt 27.
- At that point, the stack was left in the provider-mismatch state from this
  attempt:
  backend/worker `PULSEOPS_CHAT_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`.
- This pause was superseded by Attempt 28, which restored the local admin Mock
  Slack env and completed `MS-E2E-012`.
- The restore target used in Attempt 28 was:
  `PULSEOPS_ENVIRONMENT=local`,
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CALENDAR_PROVIDER=fake`,
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`,
  `PULSEOPS_VCS_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.

### Attempt 10 - 2026-06-28 10:39:25 IST

Action:

- Expanded the Mock Slack E2E coverage into the CSV-style matrix above.
- Added local compose pass-through for `PULSEOPS_CHECKIN_FANOUT_CRON` while
  preserving the default `30 9 * * 1-5`.
- Added focused compose regression coverage asserting backend and worker expose
  the check-in fanout cron override.

Expected result:

- The local stack can be launched with
  `PULSEOPS_CHECKIN_FANOUT_CRON="*/15 * * * *"` for quick schedule testing.
- Backend and worker keep mock/fake provider overrides opt-in through
  environment variables.
- Manual QA can update each CSV row with concrete pass/fail evidence.

Observed result:

- Focused compose regression passed:
  `PYTHONPATH=backend uv run pytest backend/tests/unit/test_testcontainers_compose.py --no-cov -q`
  completed with `2 passed`.
- Rebuild/recreate completed for backend and worker with:
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CALENDAR_PROVIDER=fake`,
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`,
  `PULSEOPS_VCS_PROVIDER=fake`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`,
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`, and
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
- No Docker volumes were reset. Compose still reported orphan containers
  `programmanager-scheduler-1` and `pulseops-backend-slacktest`; they were
  left untouched.
- Backend and worker both exposed the expected provider and
  `PULSEOPS_CHECKIN_FANOUT_CRON` env values.
- Live preflight passed:
  - `/health` returned `200`.
  - `/ready` returned `200`.
  - `/test/chat-simulator/status` returned `200`.
  - `/test/chat-simulator/messages` returned `200`.
- A typo check against `/test/chat-simulator/status/messages` returned `404`,
  as expected, because simulator status and messages are separate routes.
- Worker stayed up, but worker logs included repeated DBOS recovery warnings:
  `DBOS Error: System database accessed before DBOS was launched`.

Next step:

- Execute the matrix cases in priority order against the rebuilt 15-minute
  stack, and inspect whether the DBOS recovery warnings affect scheduled
  fanout.

### Attempt 11 - 2026-06-28 API Matrix Pass

Action:

- Execute API/shell-verifiable matrix cases against the rebuilt 15-minute Mock
  Slack E2E stack.
- Preserve Docker volumes.
- Use only safe/idempotent setup mutations: directory sync, adding missing mock
  member `U1002`, simulator reset, and negative reply probes.

Expected result:

- Preflight endpoints pass.
- Provider and schedule configuration are visible in backend/worker env.
- Directory sync returns stable mock Slack users.
- Simulator reset clears only mailbox state.
- Stale and unknown message replies are rejected without creating messages.

Observed result:

- `MS-E2E-001` passed:
  - Backend and worker were up.
  - `/health` returned `200` with `status=ok`, `environment=local`, and
    `tenant_id=demo`.
  - `/ready` returned `200` with all dependencies `true`.
  - `/test/chat-simulator/status` returned `200` with `enabled=true`,
    `provider=mock_slack`, and pre-reset `message_count=2`.
  - `/test/chat-simulator/messages` returned `200`; pre-reset items included
    bot `1782622890.000001` and user reply `1782622905.000002`.
- `MS-E2E-002` passed for provider configuration evidence:
  - Backend env exposed mock/fake providers:
    `mock_slack` chat/directory, `fake` calendar/issue tracker/VCS, and
    DBOS workflow provider.
  - Mock LLM `/health` returned `200`.
  - LiteLLM `/health/liveliness` returned `200`.
  - Functional dispatch remains pending in the next UI/API pass after the
    simulator reset.
- `MS-E2E-003` passed for env evidence with a log caveat:
  - Backend and worker both expose
    `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
  - Recent backend/worker logs showed DBOS recovery/nudge activity, but no
    clean schedule bootstrap line in the inspected tail.
- `MS-E2E-004` passed:
  - `POST /config/directory/sync` returned `200` with `synced_count=3` and
    `deactivated_count=0`.
  - Directory search returned mock users `U1001`, `U1002`, and `U1003`.
  - Search for `liam` returned `U1002`.
  - `POST /config/members/from-directory` added `U1002` / Liam Chen as a
    configured `mock_slack` member with `201`.
- `MS-E2E-007` passed:
  - `DELETE /test/chat-simulator/state` returned `204`.
  - Simulator status returned `message_count=0`.
  - Simulator messages returned `items=[]`.
  - `/config/members` still included configured members, including `U1001`
    and `U1002`.
  - `/pods/test%20prj-1/checkins` still returned persisted check-in data with
    `confirmed=1` and `missing=1`.
- `MS-E2E-008` passed:
  - Replying to stale pre-reset bot message `1782622890.000001` returned
    `404` with `simulator outbound message was not found`.
  - Follow-up simulator messages remained empty.
- `MS-E2E-020` passed:
  - Replying to `does-not-exist-qa-020` returned `404` with
    `simulator outbound message was not found`.
  - Follow-up simulator messages remained empty.
- Non-admin simulator API access could not be verified in this live stack:
  - The running config has `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`.
  - Adding an `Authorization` header did not change the dev principal roles;
    `/test/chat-simulator/status` still returned `200`.
  - A real non-admin rejection check requires restarting the service with
    non-admin dev roles.
- Frontend dev server was restarted in a tmux session:
  `programmanager-frontend-5173`.
  - Command:
    `npm run dev -- --host 127.0.0.1 --port 5173`.
  - Listening process: `node` PID `4359`.
  - `GET http://127.0.0.1:5173/mock-slack` returned `200`.

Next step:

- Run UI-visible `/mock-slack` cases: happy path dispatch/reply, refresh
  duplicate check, UI reset, duplicate reply behavior, member selection, and
  browser console checks.

### Attempt 12 - 2026-06-28 UI Pass And Fanout Debug

Action:

- Configure linked member `U0BB5CGGERY` / Bharat Data as Sunday-eligible
  through the supported admin API.
- Confirm there is no existing same-date schedule run for Bharat.
- Reset simulator state.
- Run Chrome/browser-visible `/mock-slack` UI checks:
  - Admin role selection and Mock Slack nav visibility.
  - Select Bharat, dispatch DM, submit reply, verify pod status.
  - Refresh twice and verify no duplicates.
  - Submit a duplicate reply against the same bot message.
  - Reset the simulator through the UI and verify app status persists.
  - Redispatch same member/date after reset to check idempotency.
- Investigate the scheduled fanout failure observed after enabling
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.

Expected result:

- UI happy path confirms Bharat's pod check-in status.
- UI refresh does not duplicate simulator messages.
- Duplicate reply does not overwrite the confirmed check-in summary.
- UI reset clears only simulator mailbox state.
- Same member/date redispatch does not create a duplicate DM.
- Scheduled fanout can start daily check-in child workflows from the 15-minute
  schedule.

Observed result:

- Setup passed:
  - `PUT /config/members/U0BB5CGGERY/checkin-preference` set
    `weekdays=[0,1,2,3,4,5,6]`.
  - No `checkin_schedule_runs` row existed for Bharat on `2026-06-28` before
    dispatch.
  - `DELETE /test/chat-simulator/state` returned `204`.
- UI navigation and role-gating check passed:
  - Direct `/mock-slack` rendered.
  - Switching the shell role to `Admin` exposed `Admin Config` and
    `Mock Slack` in the primary nav.
  - Browser console had no warnings or errors during the UI happy path.
- `MS-E2E-005` passed:
  - Selected `Bharat Data`; chat user auto-populated as `U0BB5CGGERY`.
  - Clicking `Send DM` showed the `Check-in dispatched.` toast.
  - UI timeline showed one bot `status_checkin` message.
  - Simulator API recorded bot message `1782624037.000001` with:
    - `channel_id=D0BB5CGGERY`
    - `user_id=U0BB5CGGERY`
    - `purpose=status_checkin`
    - `correlation_id=checkin-checkin-demo-U0BB5CGGERY-2026-06-28-d29b5898-4fab-4586-ac4f-7932b1499425`
  - Submitted reply:
    `Yesterday I completed the deployment checklist. Today I am validating the 15-minute Mock Slack check-in loop. No blockers.`
  - UI showed `Reply submitted.` and timeline had one bot plus one user
    message.
  - Simulator API recorded user reply `1782624062.000002`.
  - `/pods/test%20prj-1/checkins?as_of=2026-06-28` returned
    `confirmed=2`, `stale=0`, `missing=0`.
  - Bharat was `state=confirmed`, `source=confirmed`, and summary matched
    the submitted reply.
- `MS-E2E-006` passed:
  - Clicking UI `Refresh` twice left the Messages KPI at `2`.
  - Timeline remained one bot message plus one user message.
- `MS-E2E-018` passed:
  - Submitting a duplicate reply created simulator user message
    `1782624179.000003`.
  - Persisted Bharat summary remained the first confirmed reply and was not
    overwritten by the duplicate.
- `MS-E2E-030` passed:
  - UI reset confirmed with `Simulator state reset.`.
  - UI Messages KPI became `0` and timeline showed `No messages`.
  - Simulator API returned `message_count=0` and `items=[]`.
  - Bharat remained confirmed for `2026-06-28`.
  - The schedule run remained a single `sent` row for Bharat/date.
- `MS-E2E-016` passed:
  - Clicking `Send DM` again for Bharat/date after reset showed the generic
    `Check-in dispatched.` toast.
  - No new simulator message was created; API mailbox count stayed `0`.
  - The single existing `sent` schedule run was reused.
- Reply-processing warnings were observed but did not block persistence:
  - `clarification_evaluator_json_decode_failed`
  - `status_parser_json_decode_failed`
- Scheduled fanout bug found and fixed:
  - DBOS scheduled fanout with `schedule=*/15 * * * *` failed at
    `2026-06-28T05:15:00Z` and again at `05:30:00Z`.
  - DBOS rows showed
    `workflow_uuid=sched-pulseops-checkin-fanout-2026-06-28T05:15:00+00:00`,
    `status=ERROR`, and `name=pulseops_scheduled_checkin_fanout`.
  - Logs showed `AssertionError` from
    `DBOSContext.create_start_workflow_child(...): assert cur_ctx.is_workflow()`
    followed by `DBOSMaxStepRetriesExceeded`.
  - Root cause: `pulseops_checkin_fanout` was a DBOS step and called
    `DbosWorkflowScheduler.dispatch_developer_checkin()`, which starts child
    DBOS workflows. DBOS requires child workflow starts from workflow context,
    not step context.
  - Fix:
    - Extracted `developer_checkin_dispatches_for_tenant_activity()` in
      `backend/infra/workflows/checkin_fanout.py` so the retryable step only
      prepares dispatch payloads.
    - Updated DBOS fanout to call
      `pulseops_prepare_checkin_fanout` as the step, then start daily check-in
      child workflows from DBOS workflow context.
    - Reused the same daily check-in workflow starter for manual dispatch.
    - Added regression coverage for fanout starting children outside the
      prepare step.
- Targeted verification passed after the fix:
  - `uv run ruff check backend/infra/workflows/checkin_fanout.py backend/infra/adapters/workflows/dbos.py backend/tests/unit/test_agent_and_workflow.py backend/tests/unit/test_testcontainers_compose.py`
  - `uv run ruff format --check backend/infra/workflows/checkin_fanout.py backend/infra/adapters/workflows/dbos.py backend/tests/unit/test_agent_and_workflow.py backend/tests/unit/test_testcontainers_compose.py`
  - `PYTHONPATH=backend uv run pytest backend/tests/unit/test_agent_and_workflow.py backend/tests/unit/test_testcontainers_compose.py --no-cov -q`
    completed with `24 passed`.
- Rebuild/recreate completed for backend and worker with the fanout fix and
  the Mock Slack E2E env, including
  `PULSEOPS_CHECKIN_FANOUT_CRON=*/15 * * * *`.
  - No Docker volumes were reset.
  - Compose warned about orphan containers; they were left untouched.
  - Backend and worker both exposed the expected provider and schedule env.
  - `/health`, `/ready`, `/test/chat-simulator/status`, and
    `/test/chat-simulator/messages` all returned `200` after rebuild.

Next step:

- Verify a live or deterministic 15-minute schedule execution no longer fails.

### Attempt 13 - 2026-06-28 Deterministic Fanout Retest

Action:

- Trigger the existing `pulseops-checkin-fanout` DBOS schedule
  deterministically from the rebuilt backend container using the current
  application code and DBOS system database.
- Do not reset Docker volumes.
- Inspect DBOS workflow status, schedule-run rows, simulator mailbox, and logs.

Expected result:

- `pulseops-checkin-fanout` uses `*/15 * * * *`.
- Triggered schedule execution completes without the previous DBOS step-context
  assertion.
- One child daily check-in workflow is started per missing developer.
- Sunday-ineligible developers are recorded as `skipped_weekend` without
  creating simulator DMs.

Observed result:

- DBOS schedule table confirmed:
  - `schedule_name=pulseops-checkin-fanout`
  - `schedule=*/15 * * * *`
- Deterministic schedule trigger succeeded and returned:
  - `tenant_id=demo`
  - `checkin_date=2026-06-28`
  - `dispatched=5`
  - Workflow IDs:
    - `checkin-demo-dev-asha-2026-06-28-7dda55ff-3dc8-483a-948a-1e5938c5553c`
    - `checkin-demo-dev-liam-2026-06-28-3b52e382-1661-44d6-86da-56ac6f8fe986`
    - `checkin-demo-U0BAZ8DFLR1-2026-06-28-c27ccd97-d8e0-40a5-9ade-e302925666e4`
    - `checkin-demo-U1001-2026-06-28-cafde6fd-e014-4f16-bb36-490e221ceb07`
    - `checkin-demo-U1002-2026-06-28-dee0d74b-0e63-459b-8291-17fed1c8c98c`
- DBOS workflow status showed:
  - New trigger row:
    `sched-pulseops-checkin-fanout-trigger-2026-06-28T05:37:14.256446+00:00`
    with `status=SUCCESS`.
  - Natural 15-minute cron row:
    `sched-pulseops-checkin-fanout-2026-06-28T05:45:00+00:00`
    with `status=SUCCESS`.
  - Old pre-fix natural cron rows at `05:15:00Z` and `05:30:00Z` remained
    `ERROR`, which is expected historical evidence from the old image.
- `checkin_schedule_runs` for `2026-06-28` showed:
  - Bharat and Dharam remained `sent`.
  - `dev-asha`, `dev-liam`, `U0BAZ8DFLR1`, `U1001`, and `U1002` were recorded
    as `skipped_weekend` with reason
    `check-in preference excludes this weekday`.
- Simulator state stayed empty:
  - `/test/chat-simulator/status` returned `message_count=0`.
  - `/test/chat-simulator/messages` returned `items=[]`.
- Recent backend/worker logs had no matching
  `pulseops_checkin_fanout` / `pulseops_prepare_checkin_fanout` errors, no
  `AssertionError`, and no `DBOSMaxStepRetriesExceeded` after the fixed
  trigger.
- Waiting for the next natural cron tick confirmed worker-owned scheduling:
  - At `2026-06-28T05:45:20Z`, DBOS showed
    `sched-pulseops-checkin-fanout-2026-06-28T05:45:00+00:00` as
    `SUCCESS`.
  - The old pre-fix `05:15` and `05:30` rows remained `ERROR` only as
    historical rows from the old image.

Next step:

- Continue remaining negative and multi-member cases that do not require
  disruptive service restarts, then decide whether to restart with non-admin or
  disabled-simulator env for access-control cases.

### Attempt 14 - 2026-06-28 Multi-Member And Polling Checks

Action:

- Use future check-in date `2026-06-29` to avoid changing the already verified
  `2026-06-28` pod evidence.
- Reset simulator state.
- Make mock Slack members `U1001` / Asha Rao and `U1002` / Liam Chen eligible
  every day through the supported admin API.
- Dispatch both members through `/admin/workflows/checkin/dispatch`.
- Submit separate replies for each bot message.
- Attempt to reply to a user message ID.
- Observe `/mock-slack` polling behavior after external API-created messages.

Expected result:

- Multi-member messages keep distinct user/channel/correlation IDs.
- Replies process only against bot messages and persist against the matching
  check-in correlation.
- Replying to a user message ID is rejected.
- UI polling surfaces externally created messages without a full page reload.

Observed result:

- Setup passed:
  - Simulator reset returned `204`.
  - `U1001` and `U1002` preferences were updated to
    `weekdays=[0,1,2,3,4,5,6]`.
  - No `2026-06-29` schedule runs existed for `U1001` or `U1002` before
    dispatch.
- Dispatch passed:
  - `U1001` workflow:
    `checkin-demo-U1001-2026-06-29-dc2edaba-ae38-4fdd-90a8-e91559d30028`.
  - `U1002` workflow:
    `checkin-demo-U1002-2026-06-29-87a56d0a-3b8c-4cb1-be2a-3632cf4bdf3f`.
  - Simulator recorded two bot messages:
    - `1782625190.000001`, `channel_id=D1001`, `user_id=U1001`,
      `correlation_id=checkin-checkin-demo-U1001-2026-06-29-dc2edaba-ae38-4fdd-90a8-e91559d30028`.
    - `1782625190.000002`, `channel_id=D1002`, `user_id=U1002`,
      `correlation_id=checkin-checkin-demo-U1002-2026-06-29-87a56d0a-3b8c-4cb1-be2a-3632cf4bdf3f`.
- `MS-E2E-024` passed:
  - Reply to `U1001` bot message returned `processed` with user message
    `1782625251.000003`.
  - Reply to `U1002` bot message returned `processed` with user message
    `1782625251.000004`.
  - Simulator messages preserved distinct user/channel/correlation IDs.
  - `checkins` rows for the two exact correlations persisted the matching raw
    replies:
    - `U1001`: `Asha finished onboarding docs and is preparing release notes. No blockers.`
    - `U1002`: `Liam completed API smoke coverage and is checking dashboard regressions. Blocked by one flaky seed test.`
- `MS-E2E-021` passed:
  - Replying to user message `1782625251.000003` returned `404` with
    `simulator outbound message was not found`.
  - No nested user reply was created.
- `MS-E2E-025` passed with a UX caveat:
  - The bot-message selector clearly listed `U1001 - status_checkin` and
    `U1002 - status_checkin`.
  - The selected target remained `U1001` until changed manually, so testers
    must intentionally choose the target when multiple bot messages exist.
- `MS-E2E-027` passed for raw reply persistence:
  - The `U1002` blocker reply persisted on the matching check-in correlation.
  - The downstream blocker view was not separately verified in this pass.
- `MS-E2E-031` found a frontend bug:
  - The `/mock-slack` timeline polling surfaced the externally created U1001
    and U1002 messages without a full page reload.
  - The Messages KPI stayed stale at `0` until clicking `Refresh`, because the
    KPI preferred `status.data.message_count` while only the messages query
    was polling.
  - After clicking `Refresh`, the KPI updated to `4`.

Next step:

- Fix the stale Messages KPI so it reflects the polled messages list whenever
  messages data is available.

### Attempt 15 - 2026-06-28 KPI Polling Fix

Action:

- Fix the `/mock-slack` Messages KPI so it reflects `messages.data` once the
  polling messages query has loaded, while preserving the previous simulator
  status fallback before message data is available.
- Verify with frontend checks and browser UI.

Expected result:

- Externally created simulator messages appear in the timeline and update the
  Messages KPI through polling without a manual `Refresh`.
- Frontend typecheck, lint, and build pass.

Observed result:

- Code fix applied in `frontend/src/pages/MockSlackPage.tsx`:
  - Messages KPI now uses `items.length` when `messages.data` is available.
  - It falls back to `status.data?.message_count ?? items.length` before the
    messages query has loaded.
- Browser verification passed:
  - Reloaded `/mock-slack`; KPI showed `4` for existing simulator messages.
  - Created a new external `U1001` bot message for `2026-06-30` through
    `/admin/workflows/checkin/dispatch`.
  - Waited for the page polling interval without clicking `Refresh`.
  - Timeline showed the new U1001 bot message.
  - Messages KPI advanced from `4` to `5` through polling.
- Frontend verification passed:
  - `cd frontend && npm run typecheck`
  - `cd frontend && npm run lint`
  - `cd frontend && npm run build`
  - Build completed successfully with the existing Vite large-chunk warning.

Next step:

- Run final focused backend/frontend verification and summarize remaining
  restart-only access-control cases.

### Attempt 1 - 2026-06-27 23:42:45 IST

Action:

- Add explicit `PULSEOPS_CHAT_PROVIDER`,
  `PULSEOPS_DIRECTORY_PROVIDER`, `PULSEOPS_CHAT_SIMULATOR_ENABLED`, and
  `PULSEOPS_DEV_PRINCIPAL_ROLES` compose environment overrides for backend and
  worker.
- Run targeted backend simulator tests.
- Rebuild and recreate backend/worker with mock Slack simulator env.
- Run live backend preflight and Chrome-extension UI E2E.

Expected result:

- Targeted simulator tests pass.
- Live backend OpenAPI includes `/test/chat-simulator/status`.
- Simulator status/messages endpoints return `200`.
- Dispatch creates an outbound bot DM, reply is accepted, and app status
  updates.

Observed result:

- Compose config now resolves backend and worker to
  `PULSEOPS_CHAT_PROVIDER=mock_slack`,
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`, and
  `PULSEOPS_DEV_PRINCIPAL_ROLES=admin` when launched with the planned env.
- Targeted backend simulator suite passed:
  `PYTHONPATH=backend uv run pytest backend/tests/unit/test_chat_simulator_api.py backend/tests/unit/test_registry_providers.py backend/tests/unit/test_mock_slack_adapter.py --no-cov -q`
  completed with `16 passed, 1 warning`.
- Docker rebuild/recreate completed for backend and worker with mock Slack env.
  No Docker volumes were reset. Docker reported existing orphan containers
  (`programmanager-scheduler-1`, `pulseops-backend-slacktest`), which were left
  untouched.
- Live backend preflight passed:
  - `/health` returned `200` with `environment=local` and `tenant_id=demo`.
  - Backend and worker containers both expose
    `PULSEOPS_CHAT_PROVIDER=mock_slack`,
    `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`, and
    `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`.
  - `/openapi.json` includes all simulator routes:
    `/test/chat-simulator/status`, `/test/chat-simulator/messages`,
    `/test/chat-simulator/messages/{message_id}/reply`, and
    `/test/chat-simulator/state`.
  - `/test/chat-simulator/status` returned `200` with
    `provider=mock_slack`, `enabled=true`, and `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.
- Admin UI directory setup completed:
  - Opened `/admin` in the existing Chrome-extension Playwright session.
  - Confirmed active role was `Admin`.
  - Clicked `Directory`, then `Sync directory`.
  - Directory API returned mock Slack users `U1001`, `U1002`, and `U1003`.
  - Selected Asha Rao and clicked `Add selected`.
  - `/config/members` now includes `U1001` / Asha Rao with
    `source=mock_slack` and `chat_external_id=U1001`.
- Mock Slack UI dispatch reached a new blocker:
  - Opened `/mock-slack`; page showed tenant `demo`, `4 configured` members,
    Asha Rao selected, and chat user `U1001`.
  - Confirmed simulator reset through the UI and verified
    `/test/chat-simulator/messages` returned `{"items":[]}`.
  - Clicked `Send DM`; dispatch API returned `200`.
  - No simulator message appeared after 10 polls over approximately 20 seconds.
  - Backend logs show DBOS launched for the dispatch, then immediately shut
    down before the async workflow could run, ending with
    `RuntimeError: cannot schedule new futures after shutdown`.
- Applied DBOS dispatch lifecycle fix:
  - `dispatch_developer_checkin` and `dispatch_sync` now keep the DBOS runtime
    alive after starting asynchronous workflows.
  - Schedule bootstrap methods still use transient runtime cleanup.
  - Focused tests passed:
    `PYTHONPATH=backend uv run pytest backend/tests/unit/test_agent_and_workflow.py::test_dbos_dispatch_keeps_runtime_alive_for_started_workflows backend/tests/unit/test_chat_simulator_api.py backend/tests/unit/test_registry_providers.py backend/tests/unit/test_mock_slack_adapter.py --no-cov -q`
    completed with `17 passed, 1 warning`.

Next step:

- Rebuild/recreate backend and worker with the DBOS dispatch fix, then rerun
  `/mock-slack` dispatch and reply.

### Attempt 2 - 2026-06-28 00:03:08 IST

Action:

- Update DBOS manual check-in dispatch to await the returned workflow handle so
  the daily check-in workflow completes before the dispatch API returns.
- Keep generic sync dispatch asynchronous.
- Update focused unit coverage for the DBOS dispatch lifecycle.
- Run targeted backend checks.
- Rebuild/recreate backend and worker with mock Slack simulator env.
- Repeat live backend preflight and Chrome-extension `/mock-slack` E2E.

Expected result:

- Focused backend tests pass.
- Live backend remains configured with `mock_slack` providers and simulator
  routes.
- Clicking `Send DM` records a visible outbound bot DM in simulator state.
- Submitting a reply posts through `/webhooks/chat/mock_slack`.
- Developer status/check-in UI reflects the reply.

Observed result:

- Focused backend suite passed:
  `PYTHONPATH=backend uv run pytest backend/tests/unit/test_agent_and_workflow.py::test_dbos_dispatch_keeps_runtime_alive_for_started_workflows backend/tests/unit/test_chat_simulator_api.py backend/tests/unit/test_registry_providers.py backend/tests/unit/test_mock_slack_adapter.py --no-cov -q`
  completed with `17 passed, 1 warning`.
- Docker rebuild/recreate completed for backend and worker with the DBOS
  dispatch completion fix. No Docker volumes were reset. Docker again reported
  existing orphan containers (`programmanager-scheduler-1`,
  `pulseops-backend-slacktest`), which were left untouched.
- The frontend dev server was not listening on `127.0.0.1:5173` after the
  rebuild loop. Restarted it with
  `npm run dev -- --host 127.0.0.1 --port 5173` from `frontend/`.
- Live backend preflight passed:
  - Backend and worker containers both expose
    `PULSEOPS_CHAT_PROVIDER=mock_slack`,
    `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
    `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`, and
    `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`.
  - `/health` returned `200` with `environment=local` and `tenant_id=demo`.
  - `/openapi.json` includes `/test/chat-simulator/status`.
  - `/test/chat-simulator/status` returned `200` with
    `provider=mock_slack`, `enabled=true`, and `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.
- Chrome-extension UI retest reached a data/setup blocker:
  - `/admin` loaded and `Sync directory` returned `200`.
  - Directory users include mock Slack IDs `U1001`, `U1002`, and `U1003`;
    configured members include `U1001` / Asha Rao with `source=mock_slack`.
  - `/mock-slack` reset simulator state through the UI; messages were empty.
  - Selected linked member `U0BB5CGGERY` / Bharat Data and clicked `Send DM`.
  - Dispatch API returned `200`, but no bot message appeared after 60 seconds.
  - Database evidence showed the workflow recorded
    `checkin_schedule_runs.status=skipped_weekend` for `U0BB5CGGERY` on
    `2026-06-27`, with reason `check-in preference excludes this weekday`.
  - The same Saturday skip was already present for `U1001`, so both attempted
    developers had existing schedule runs that would return early without
    sending a DM.

Next step:

- Configure a linked member that has no schedule run yet to allow Saturday,
  reset simulator state, then rerun `/mock-slack` dispatch and reply.

### Attempt 3 - 2026-06-28 00:12:03 IST

Action:

- Use the existing Chrome session and backend API to set a linked member's
  check-in preference to include Saturday.
- Use a linked member without an existing `2026-06-27` schedule run so the
  workflow cannot short-circuit on prior weekend-skip state.
- Reset simulator state and rerun the Mock Slack dispatch/reply path.

Expected result:

- Dispatch records a bot DM in simulator state.
- Reply posts through `/test/chat-simulator/messages/{message_id}/reply` and
  processes through `/webhooks/chat/mock_slack`.
- Pod check-in UI/API shows the selected member as confirmed with a status
  summary.

Observed result:

- Set `U0BAQ1H9UTZ` / Dharam check-in preference to include Saturday
  (`weekdays=[0,1,2,3,4,5]`).
- `/mock-slack` reset simulator state through the UI and messages were empty.
- Selected Dharam and clicked `Send DM`.
- Dispatch did not complete within the UI wait window. Backend logs show the
  workflow reached `pulseops_start_daily_checkin`, then failed availability
  lookup because the container was still using the real Google calendar adapter:
  `ProviderUnavailable: calendar credentials are not configured`.
- The failed DBOS step retried three times, then the dispatch returned
  `500 Internal Server Error`.
- Added explicit backend/worker compose pass-through:
  `PULSEOPS_CALENDAR_PROVIDER: ${PULSEOPS_CALENDAR_PROVIDER:-google}`.
- Rebuilt/recreated backend and worker with
  `PULSEOPS_CALENDAR_PROVIDER=fake` in addition to mock Slack provider env.
- Live preflight passed again:
  - Backend and worker both expose `PULSEOPS_CALENDAR_PROVIDER=fake`,
    `PULSEOPS_CHAT_PROVIDER=mock_slack`,
    `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
    `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`, and
    `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`.
  - `/health` returned `200`.
  - `/test/chat-simulator/status` returned `200` with
    `provider=mock_slack`, `enabled=true`, and `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.

Next step:

- Rerun Chrome-extension `/mock-slack` dispatch/reply for Dharam.

### Attempt 6 - 2026-06-28 10:22:37 IST

Action:

- Rerun Chrome-extension `/mock-slack` dispatch/reply for Dharam after the
  Sunday preference and stale skip-row setup fix.

Expected result:

- Dispatch records a visible outbound bot DM in simulator state.
- Reply can be submitted against the selected bot message.
- The check-in API/UI shows Dharam as confirmed with the submitted status.

Observed result:

- Live preflight passed:
  - `/health` returned `200` with `environment=local` and `tenant_id=demo`.
  - `/test/chat-simulator/status` returned `200` with
    `provider=mock_slack`, `enabled=true`, and `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.
  - Dharam's preference remained `weekdays=[0,1,2,3,4,5,6]`.
- Chrome-extension `/mock-slack` selected `U0BAQ1H9UTZ` / Dharam, reset
  simulator state, then clicked `Send DM`.
- Dispatch failed before creating a simulator message:
  - Browser saw `Failed to fetch` because the dispatch endpoint returned
    `500` without CORS headers.
  - A direct POST with the same body returned `500 Internal Server Error`.
  - Backend logs show Jira active-issues lookup returned `410 Gone`.
  - Root cause was `ProviderUnavailable: issue tracker request failed`.
  - DBOS raised `DBOSMaxStepRetriesExceeded` for
    `pulseops_start_daily_checkin` after three retries.
- Running backend and worker env showed
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=jira`; chat/directory/calendar were already
  `mock_slack` / `mock_slack` / `fake`.
- No outbound simulator message was created, so no reply could be submitted.
- Final simulator messages remained empty.
- `/pods/test%20prj-1/checkins?as_of=2026-06-28` showed Dharam as
  `state=missing`, `source=unknown`, `status_as_of=null`, with summary
  `No check-in status is available.`

Next step:

- Make the local mock Slack E2E stack use fake read providers for check-in
  context, then rebuild/recreate backend and worker and retest.

### Attempt 7 - 2026-06-28 10:27:26 IST

Action:

- Add compose pass-through for fake read-provider overrides needed by local
  Mock Slack E2E.
- Add focused regression coverage for backend/worker provider env defaults.
- Run the targeted compose unit test.

Expected result:

- Backend and worker compose services can be launched with fake issue tracker
  and fake VCS providers without editing compose.
- The regression test fails if future compose changes drop provider override
  pass-through for backend or worker.

Observed result:

- Updated `docker-compose.yml` for backend and worker with:
  - `PULSEOPS_ISSUE_TRACKER_PROVIDER:
    ${PULSEOPS_ISSUE_TRACKER_PROVIDER:-jira}`
  - `PULSEOPS_VCS_PROVIDER: ${PULSEOPS_VCS_PROVIDER:-github}`
- Added `backend/tests/unit/test_testcontainers_compose.py` coverage that
  parses `docker-compose.yml` and asserts backend and worker expose overrideable
  defaults for chat, directory, calendar, issue tracker, and VCS providers.
- Targeted test passed:
  `PYTHONPATH=backend uv run pytest backend/tests/unit/test_testcontainers_compose.py --no-cov`
  completed with `2 passed`.

Next step:

- Rebuild/recreate backend and worker with
  `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake` and
  `PULSEOPS_VCS_PROVIDER=fake` alongside the existing mock Slack and fake
  calendar env, then rerun live dispatch/reply.

### Attempt 8 - 2026-06-28 10:29:17 IST

Action:

- Rebuild and recreate backend and worker with local Mock Slack E2E provider
  env.
- Do not reset Docker volumes.
- Verify container env and simulator endpoints before retesting the UI.

Expected result:

- Backend and worker use fake read providers for issue tracker, VCS, and
  calendar while still using mock Slack chat/directory.
- Simulator endpoints stay available and empty after the rebuild.

Observed result:

- Rebuild/recreate completed with:
  `PULSEOPS_CHAT_PROVIDER=mock_slack PULSEOPS_DIRECTORY_PROVIDER=mock_slack PULSEOPS_CALENDAR_PROVIDER=fake PULSEOPS_ISSUE_TRACKER_PROVIDER=fake PULSEOPS_VCS_PROVIDER=fake PULSEOPS_CHAT_SIMULATOR_ENABLED=true PULSEOPS_DEV_PRINCIPAL_ROLES=admin docker compose up -d --build --force-recreate --no-deps backend worker`.
- No Docker volumes were reset.
- Backend and worker containers recreated and started successfully.
- Backend and worker both expose:
  - `PULSEOPS_CALENDAR_PROVIDER=fake`
  - `PULSEOPS_CHAT_PROVIDER=mock_slack`
  - `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`
  - `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`
  - `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`
  - `PULSEOPS_ISSUE_TRACKER_PROVIDER=fake`
  - `PULSEOPS_VCS_PROVIDER=fake`
- Endpoint checks passed:
  - `/health` returned `200`.
  - `/test/chat-simulator/status` returned `200` with
    `enabled=true`, `tenant_id=demo`, `provider=mock_slack`, and
    `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.
- Docker still reported orphan containers
  `programmanager-scheduler-1` and `pulseops-backend-slacktest`; they were
  left untouched.
- Worker logs still show repeated DBOS duplicate-registration warnings for
  `_dbos_debouncer_workflow`; no endpoint errors were observed.

Next step:

- Rerun Chrome-extension `/mock-slack` dispatch/reply for Dharam with fake
  issue/VCS/calendar providers active.

### Attempt 9 - 2026-06-28 10:32:32 IST

Action:

- Run the Chrome-extension `/mock-slack` happy path with the rebuilt backend and
  worker using mock Slack plus fake issue tracker, VCS, and calendar providers.
- Reset simulator state, dispatch to Dharam, submit a reply, and verify pod
  check-in status.

Expected result:

- Dispatch creates an outbound mock Slack bot DM.
- Reply is accepted by the simulator API and processed through the webhook
  correlation path.
- Pod check-in API shows Dharam as confirmed with the submitted status summary.

Observed result:

- Live preflight passed:
  - `/health` returned `200`.
  - `/ready` returned `200`, including Redis readiness.
  - `/test/chat-simulator/status` returned `200` with
    `enabled=true`, `tenant_id=demo`, `provider=mock_slack`, and
    `message_count=0`.
  - Initial `/pods/test%20prj-1/checkins?as_of=2026-06-28` showed Dharam as
    `missing`, with pod totals `confirmed=0`, `stale=0`, and `missing=2`.
- Chrome-extension `/mock-slack` flow passed:
  - UI reset called `DELETE /test/chat-simulator/state` and returned `204`.
  - Selected member `U0BAQ1H9UTZ` / Dharam.
  - `POST /admin/workflows/checkin/dispatch` returned `200`.
  - Workflow id:
    `checkin-demo-U0BAQ1H9UTZ-2026-06-28-590f1675-d179-482c-a74d-f025bf624f95`.
- Bot DM appeared in simulator state:
  - `message_id=1782622890.000001`
  - `channel_id=D0BAQ1H9UTZ`
  - `user_id=U0BAQ1H9UTZ`
  - `direction=bot`
  - `purpose=status_checkin`
  - `correlation_id=checkin-checkin-demo-U0BAQ1H9UTZ-2026-06-28-590f1675-d179-482c-a74d-f025bf624f95`
  - `metadata={"purpose":"status_checkin"}`
- Submitted reply:
  `Yesterday I finished the billing API integration. Today I am validating the mock Slack E2E flow. No blockers.`
- Reply API passed:
  `POST /test/chat-simulator/messages/1782622890.000001/reply` returned
  `200` with `status=processed`,
  `message_id=1782622905.000002`, and
  `processed_message_id=1782622905.000002`.
- Final simulator state had `message_count=2`, including the user reply:
  - `message_id=1782622905.000002`
  - `channel_id=D0BAQ1H9UTZ`
  - `user_id=U0BAQ1H9UTZ`
  - `direction=user`
  - `reply_to_message_id=1782622890.000001`
  - matching check-in `correlation_id`
  - `metadata={"source":"mock_slack"}`
- Final check-in verification passed:
  - `/pods/test%20prj-1/checkins?as_of=2026-06-28` returned `200`.
  - Pod totals were `confirmed=1`, `stale=0`, and `missing=1`.
  - Dharam was `state=confirmed`, `source=confirmed`, and
    `status_as_of=2026-06-28`.
  - Dharam's summary matched the submitted reply exactly.
- Browser console had no errors beyond React DevTools info.
- Backend logs showed expected route traffic and `status_reply_received`.
- Backend emitted `clarification_evaluator_json_decode_failed` and
  `status_parser_json_decode_failed` warnings during reply processing, but they
  did not block processing or persistence.

Next step:

- Final targeted regression tests passed; summarize results.

Final verification:

- Focused regression suite passed:
  `PYTHONPATH=backend uv run pytest backend/tests/unit/test_agent_and_workflow.py::test_dbos_dispatch_keeps_runtime_alive_for_started_workflows backend/tests/unit/test_chat_simulator_api.py backend/tests/unit/test_registry_providers.py backend/tests/unit/test_mock_slack_adapter.py backend/tests/unit/test_testcontainers_compose.py --no-cov -q`
  completed with `19 passed, 1 warning`.
- The only warning was an existing Starlette `TestClient` deprecation warning
  from `fastapi.testclient`.

### Attempt 4 - 2026-06-28 10:09:22 IST

Action:

- Rerun the live Mock Slack E2E path after the Attempt 3 rebuild configured
  backend and worker with `PULSEOPS_CALENDAR_PROVIDER=fake`.
- Use the existing browser session, reset simulator state, dispatch a DM for a
  linked member, submit a reply, and verify developer status/check-in state.

Expected result:

- Dispatch records a visible outbound bot DM in simulator state.
- Reply posts through the simulator API and mock Slack webhook path.
- The selected member's developer status updates in the app.

Observed result:

- Live preflight passed:
  - Backend and worker both expose `PULSEOPS_CALENDAR_PROVIDER=fake`,
    `PULSEOPS_CHAT_PROVIDER=mock_slack`,
    `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`,
    `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`, and
    `PULSEOPS_DEV_PRINCIPAL_ROLES=admin`.
  - `/health` returned `200` with `environment=local` and
    `tenant_id=demo`.
  - `/test/chat-simulator/status` returned `200` with
    `provider=mock_slack`, `enabled=true`, and `message_count=0`.
  - `/test/chat-simulator/messages` returned `200` with an empty item list.
- The frontend dev server was not listening on `127.0.0.1:5173`; the testing
  agent temporarily started it, verified `/mock-slack`, and stopped it after
  the retest.
- Chrome-extension `/mock-slack` retest selected `U0BAQ1H9UTZ` / Dharam,
  reset simulator state through the UI, then clicked `Send DM`.
- Dispatch API returned `200`, but no simulator message appeared after 30
  polls over approximately 60 seconds.
- Database evidence showed the workflow created a successful DBOS run but
  recorded `checkin_schedule_runs.status=skipped_weekend` for
  `U0BAQ1H9UTZ` on `2026-06-28`, with reason
  `check-in preference excludes this weekday`.
- No `checkin_correlations` or `checkins` rows were created for Dharam, so no
  reply could be submitted.
- Backend and worker logs showed no runtime/provider errors during this retest.

Next step:

- Configure the selected linked member to allow Sunday or dispatch against an
  eligible date, then rerun `/mock-slack` dispatch/reply.

### Attempt 5 - 2026-06-28 10:17:34 IST

Action:

- Use the existing backend admin API to make `U0BAQ1H9UTZ` / Dharam eligible
  for Sunday check-ins.
- Verify whether the previous `2026-06-28` `skipped_weekend` row would block a
  rerun.
- Remove only the stale local skip row if it has no dependent check-in,
  correlation, or nudge rows.

Expected result:

- Dharam's check-in preference includes Sunday.
- No stale `2026-06-28` skip row remains to short-circuit the next dispatch.
- Mock Slack simulator state stays empty and ready for the next retest.

Observed result:

- Updated Dharam through supported admin API:
  `PUT /config/members/U0BAQ1H9UTZ/checkin-preference` with
  `weekdays=[0,1,2,3,4,5,6]`.
- Verified resulting preference row:
  - `tenant_id=demo`
  - `developer_id=U0BAQ1H9UTZ`
  - `local_time=09:30:00`
  - `timezone=UTC`
  - `weekdays={"items":[0,1,2,3,4,5,6]}`
  - `reply_wait_seconds=14400`
  - `final_reply_wait_seconds=28800`
- Confirmed the existing `2026-06-28` skip row would still block a rerun
  because schedule-run lookup happens before weekday preference evaluation.
- Deleted only the stale `checkin_schedule_runs` row for
  `tenant_id=demo`, `developer_id=U0BAQ1H9UTZ`, `checkin_date=2026-06-28`,
  `status=skipped_weekend`, and reason
  `check-in preference excludes this weekday`, guarded by `NOT EXISTS` checks
  for linked check-in, correlation, and nudge rows.
- Final setup verification found no `checkin_schedule_runs` row remaining for
  Dharam on `2026-06-28`.
- Mock Slack remained enabled with `message_count=0`.

Next step:

- Rerun Chrome-extension `/mock-slack` dispatch/reply for Dharam.

## Test Run Evidence

Date: 2026-06-27

Browser path: existing Chrome session through the Playwright Chrome extension.
No isolated browser was used.

URLs exercised:

```text
http://127.0.0.1:5173/me
http://127.0.0.1:5173/admin
http://127.0.0.1:5173/mock-slack
http://127.0.0.1:8000/health
http://127.0.0.1:8000/test/chat-simulator/status
http://127.0.0.1:8000/test/chat-simulator/messages
```

Observed working behavior:

- Frontend loaded successfully.
- App showed backend health as `ok`.
- App showed environment/tenant as `local / demo`.
- Active role showed `Viewing as Admin`.
- `/mock-slack` rendered.
- Mock Slack page showed tenant `demo`.
- Mock Slack page showed `3 configured` members.
- Visible members included `Bharat Data`, `Dharam`, and `Shubham Singh`.
- Clicking `Send DM` called
  `POST http://127.0.0.1:8000/admin/workflows/checkin/dispatch`.
- Dispatch API returned `200`.
- UI displayed `Check-in dispatched.`

Observed blocker:

- `GET http://127.0.0.1:8000/test/chat-simulator/status` returned `404`.
- `GET http://127.0.0.1:8000/test/chat-simulator/messages` returned `404`.
- Browser console logged repeated simulator API `404` failures.
- No outbound bot DM appeared in the Mock Slack console.
- Message selector only showed `Select message`.
- `Submit reply` remained disabled.
- `/me` still showed `No developer status data is available`.

Screenshot:

```text
/tmp/mock-slack-e2e.png
```

## Required Retest Setup

Restart the backend process or container with the simulator-enabled local
configuration:

```bash
PULSEOPS_CHAT_PROVIDER=mock_slack
PULSEOPS_DIRECTORY_PROVIDER=mock_slack
PULSEOPS_CHAT_SIMULATOR_ENABLED=true
PULSEOPS_DEV_PRINCIPAL_ROLES=admin
```

Before rerunning the UI test, confirm these checks pass:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/test/chat-simulator/status
curl -i http://127.0.0.1:8000/test/chat-simulator/messages
```

Expected result:

- `/health` returns `200`.
- `/test/chat-simulator/status` returns `200` for an admin caller.
- `/test/chat-simulator/messages` returns `200` for an admin caller.
- The status response indicates the simulator is enabled.

If the simulator endpoints still return `404`, verify the running backend image
or process includes the mock Slack implementation commit and that the backend
environment is `local`.

## What Still Needs To Be Tested

### Happy Path

- Open the frontend in the existing Chrome session.
- Confirm the active role is `Admin`.
- Sync the mock directory.
- Add a mock member from the directory.
- Open `/mock-slack`.
- Dispatch a check-in from the Mock Slack console.
- Confirm the outbound bot DM appears in the message list.
- Confirm the DM includes the expected user, channel, message ID, timestamp, and
  purpose metadata.
- Submit a reply against the selected bot message.
- Confirm the reply is accepted by the simulator API.
- Confirm the dashboard or check-in status view shows the updated developer
  status.
- Refresh the Mock Slack console and confirm the injected reply remains visible.

### Simulator State Controls

- Click `Refresh` and confirm message/status data reloads without creating
  duplicate messages.
- Click `Reset` and confirm simulator messages are cleared.
- Confirm `Reset` does not delete persisted members, check-ins, or developer
  statuses.
- In container mode, confirm backend, worker, and scheduler see the same
  simulator messages through Redis-backed state.

### Access Control

- Confirm `/mock-slack` is visible for an admin in local/dev mode.
- Confirm `/mock-slack` is hidden or inaccessible for non-admin roles.
- Confirm simulator API routes return `404` when
  `PULSEOPS_CHAT_SIMULATOR_ENABLED=false`.
- Confirm simulator API routes return `404` outside the local environment.
- Confirm simulator API routes reject non-admin callers.

### Provider Configuration

- Confirm `PULSEOPS_CHAT_PROVIDER=mock_slack` records outbound DMs in the
  simulator.
- Confirm `PULSEOPS_DIRECTORY_PROVIDER=mock_slack` syncs Slack-like mock users.
- Confirm the mock directory provides stable IDs such as `U1001`, `U1002`, and
  `U1003`.
- Confirm real Slack credentials are not required for the full local flow.

### Reply Processing

- Reply to the newest outbound bot message and verify correlation succeeds.
- Reply with a short status update and verify the resulting status text.
- Reply with blocker text and verify blocker/status classification if supported
  by the workflow.
- Attempt to reply without selecting a message and confirm the UI prevents it.
- Attempt to reply after reset and confirm stale message IDs are not accepted.

### Regression Checks

- Confirm normal admin check-in dispatch still works with non-mock providers
  when mock Slack is disabled.
- Confirm `/webhooks/chat/mock_slack` processes Slack-shaped payloads when the
  simulator creates a reply.
- Confirm frontend type generation still matches the backend OpenAPI schema.
- Confirm architecture boundary tests still pass with provider names kept out of
  backend `api` and `core` source files.

## Exit Criteria

The Mock Slack E2E flow can be considered verified when:

- The simulator endpoints return `200` in local admin mode.
- A check-in dispatch creates a visible outbound mock DM.
- A submitted mock reply is processed through the webhook/correlation path.
- The member's developer status updates in the app UI.
- Reset clears simulator state without deleting persisted application data.
