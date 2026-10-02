# Slack setup

OpenProgram sends check-in DMs through the Slack Web API and receives developer
replies through one of two inbound transports, picked by
`OPENPROGRAM_SLACK_INBOUND_TRANSPORT`:

| | `http` (default) | `socket` |
|---|---|---|
| How replies arrive | Slack POSTs each event to `https://<api-host>/webhooks/chat/slack` | The worker holds an outbound Socket Mode WebSocket to Slack |
| Network | A **public** HTTPS endpoint Slack can reach, plus WAF on `/webhooks/*` | Outbound HTTPS/WSS to `*.slack.com` only |
| Inbound credential | Signing secret `OPENPROGRAM_SLACK_SIGNING_SECRET` | App-level token `OPENPROGRAM_SLACK_APP_TOKEN` (`xapp-`) |
| Runs in | `backend` API | `worker` (`python -m infra.workflows.worker`) |

Both paths feed the same intake (`ServiceRegistry.accept_chat_event`), so
mapping, `event_id` redelivery dedup, reply coalescing and the inbound sweeper
behave identically. `http` is the default and Slack's recommended production
mode; it needs an approved public ingress. `socket` needs none, which suits
local development, UAT, and a fallback while the ingress is unavailable.

For local work without Slack, use the simulator instead
(`OPENPROGRAM_CHAT_PROVIDER=mock_slack`, see [mock-slack.md](../../mock-slack.md)).

## Slack app configuration (both transports)

At <https://api.slack.com/apps> → **Create New App** → *From scratch*, one app per environment:

1. **OAuth & Permissions → Bot Token Scopes:** `chat:write`, `im:write`,
   `im:history`, `users:read`. Install to the workspace and copy the
   *Bot User OAuth Token* (`xoxb-…`) into `OPENPROGRAM_SLACK_BOT_TOKEN`.
2. **App Home → Show Tabs:** enable the *Messages Tab* and tick
   *Allow users to send Slash commands and messages from the messages tab*.
   Without it developers cannot reply to the bot at all.
3. **Event Subscriptions:** enable, and under *Subscribe to bot events* add
   `message.im`.

## Socket Mode (`socket`)

1. **Socket Mode:** enable it. Slack asks you to create an app-level token; give
   it the `connections:write` scope and copy it (`xapp-…`) into
   `OPENPROGRAM_SLACK_APP_TOKEN`. Event Subscriptions then needs no Request URL.
2. Configure the worker and API:

   ```bash
   OPENPROGRAM_CHAT_PROVIDER=slack
   OPENPROGRAM_SLACK_INBOUND_TRANSPORT=socket
   OPENPROGRAM_SLACK_BOT_TOKEN=xoxb-...
   OPENPROGRAM_SLACK_APP_TOKEN=xapp-...
   ```

3. Allow outbound HTTPS to `slack.com` and WSS to `*.slack.com`. Behind a
   corporate proxy, set `HTTPS_PROXY` (and `NO_PROXY` for internal hosts) on the
   worker: both the Web API client and the WebSocket honour it.

**Verify:** the worker logs `slack.socket.connected`, after which `GET /ready`
reports `"slack_socket": true`; a DM to the bot is recorded as a check-in reply.

**Operating it**

- Each worker task holds one connection and Slack spreads events across them
  (an app may hold up to 10). Run **two** worker tasks so a rolling deploy never
  leaves Slack with no open socket; redeliveries across connections are deduped
  by `event_id`.
- Slack refreshes connections every few hours. The listener reconnects
  immediately on a refresh and backs off 1 s → 60 s on network errors.
- An event is acknowledged only after it is durably accepted. If intake fails,
  the envelope is left unacked and Slack redelivers it.
- `/ready` → `slack_socket` reads a Redis heartbeat (90 s TTL) that every
  connected worker refreshes. It goes `false` only when no worker has held a
  socket for 90 s.

**When it is down**

| Symptom | Cause | Fix |
|---|---|---|
| `slack_provider: false`, worker logs `slack.socket.misconfigured` … `slack_app_token … is required` | App token not set | Set `OPENPROGRAM_SLACK_APP_TOKEN`; the worker keeps running other workflows meanwhile |
| `slack.socket.misconfigured` … `not authorized for apps.connections.open` | Wrong token type (a bot token in the app-token slot) or revoked token | Use the `xapp-` token with `connections:write` |
| `slack.socket.misconfigured` … `Socket Mode is disabled` | Socket Mode switched off in the app config | Re-enable it, or move to `http` |
| repeated `slack.socket.connection_lost` | Egress blocked or proxy not set | Check the proxy and the allow-list for `*.slack.com` |

## Events API over HTTP (`http`, default)

1. Leave **Socket Mode** off. Under **Event Subscriptions** set the Request URL
   to `https://<api-host>/webhooks/chat/slack`. Slack sends a `url_verification`
   challenge, which the backend echoes.
2. Copy **Basic Information → Signing Secret** into
   `OPENPROGRAM_SLACK_SIGNING_SECRET`, and set
   `OPENPROGRAM_SLACK_INBOUND_TRANSPORT=http`.
3. The route must be reachable from the internet over HTTPS. Put a rate-based WAF
   rule on `/webhooks/*`: the path is CSRF-exempt by design, and only the
   signature check protects it.

**Verify:** Slack accepts the Request URL; a DM reply reaches the backend and is
recorded; a request with a bad signature gets 401.

The webhook route stays mounted under `socket` too, and it still requires a
valid signature. With no signing secret configured, it rejects every request
with 401.

## Counterpart DMs (on by default)

When a developer's check-in reply asks something of another person ("need Liam
to review the PR"), OpenProgram resolves that person in the directory and DMs
them once about the request. Their reply in that DM thread acknowledges the
request, or resolves it, in which case the developer is told. A person who
cannot be found, or is ambiguous, gets no DM and the request stays visible to
the developer who asked.

This needs no extra Slack scope: it uses the same `chat:write` and `im:write`
the check-ins use, and `im:history` to read the reply. To record these requests
without messaging anyone, set:

```bash
OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY=false
```

on the API and the worker (both route replies). Existing requests are
unaffected; the board and the "Raised by you" list work the same either way.
