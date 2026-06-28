# Mock Slack Manual Testing

Use the Mock Slack console to test check-in workflows locally without real Slack
credentials. The simulator records outbound bot DMs and lets an admin submit a
reply through the same webhook correlation path used by chat webhooks.

## Local Setup

Add the local E2E settings to your backend environment:

```bash
PULSEOPS_CHAT_PROVIDER=mock_slack
PULSEOPS_DIRECTORY_PROVIDER=mock_slack
PULSEOPS_CHAT_SIMULATOR_ENABLED=true
PULSEOPS_DEV_PRINCIPAL_ROLES=admin
```

For the frontend, the Mock Slack navigation item is shown to admins in
local/dev mode. If you need to force the page on in another local frontend
build, set:

```bash
VITE_ENABLE_CHAT_SIMULATOR=true
```

Start the local stack as usual. In container mode, Redis stores the simulator
mailbox so the backend, worker, and scheduler can see the same mock messages.
In memory mode, simulator state only lives in the current backend process.

## UI Flow

1. Open the frontend and switch the active role to `Admin`.
2. Open the admin area.
3. Run directory sync so the mock directory users are available.
4. Add a member from the synced directory. Mock users use Slack-like IDs such as
   `U1001`, `U1002`, and `U1003`.
5. Open `/mock-slack`.
6. In the dispatch panel, choose the member and click `Send DM`.
7. Confirm the outbound bot DM appears in the messages list.
8. Select that message, enter a reply, and submit it.
9. Open the dashboard or check-in status view and verify the member status was
   updated from the reply.

Example reply text:

```text
Finished the API shell; no blockers.
```

## Console Actions

- `Send DM`: dispatches a check-in using the existing admin check-in dispatch
  API.
- `Refresh`: reloads simulator status, recorded messages, and related app data.
- `Submit Reply`: injects a user reply for the selected outbound bot message.
- `Reset`: clears simulator state, including recorded mock Slack messages.

`Reset` only clears simulator mailbox state. It does not delete persisted app
check-ins, developer statuses, members, or directory records.

## Automation API

The UI uses provider-neutral local test-support endpoints:

```text
GET    /test/chat-simulator/status
GET    /test/chat-simulator/messages
POST   /test/chat-simulator/messages/{message_id}/reply
DELETE /test/chat-simulator/state
```

These routes are only available when all of the following are true:

- `PULSEOPS_CHAT_SIMULATOR_ENABLED=true`
- The backend environment is local.
- The caller is authorized as an admin.

When the simulator is disabled or unavailable for the current backend
environment, these routes return `404`. When the simulator is enabled but the
caller is not an admin, they return `403`.

## Troubleshooting

- Mock Slack is not visible in navigation: confirm the active role is `Admin`
  and the frontend is running in local/dev mode, or set
  `VITE_ENABLE_CHAT_SIMULATOR=true`.
- `/test/chat-simulator/status` returns `404`: confirm the simulator is enabled,
  the backend environment is local, and the request is made as an admin.
- `/test/chat-simulator/status` returns `403`: confirm the active user has the
  admin role.
- Directory sync asks for real Slack configuration: confirm
  `PULSEOPS_DIRECTORY_PROVIDER=mock_slack`.
- No members appear in the dispatch form: sync the directory and add a member
  first.
- A DM was dispatched but no mock message appears: refresh the console and check
  that the backend and worker processes are running with
  `PULSEOPS_CHAT_PROVIDER=mock_slack`.
- A submitted reply does not update status: send a fresh check-in and reply to
  the newest outbound bot message so the correlation metadata matches an active
  workflow.

## Safety Notes

Mock Slack is for local manual testing only. It does not emulate all Slack
behavior and must not be used as a production Slack substitute.
