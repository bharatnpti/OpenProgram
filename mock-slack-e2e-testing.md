# Mock Slack E2E Testing Status

## Current Status

Status: `Verified`

Attempt 9 verified the Chrome-extension `/mock-slack` happy path end to end:
dispatch created an outbound mock Slack DM, a reply was processed through the
simulator/webhook path, and Dharam's pod check-in status updated to confirmed.

## Iteration Log

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
