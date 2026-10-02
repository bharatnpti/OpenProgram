# Slack setup

OpenProgram sends check-in DMs through the Slack Web API and receives developer
replies through one of two inbound transports, picked by
`OPENPROGRAM_SLACK_INBOUND_TRANSPORT`:

| | `socket` (default) | `http` |
|---|---|---|
| How replies arrive | The worker holds an outbound Socket Mode WebSocket to Slack | Slack POSTs each event to `https://<api-host>/webhooks/chat/slack` |
| Network | Outbound HTTPS/WSS to `*.slack.com` only | A **public** HTTPS endpoint Slack can reach, plus WAF on `/webhooks/*` |
| Inbound credential | App-level token `OPENPROGRAM_SLACK_APP_TOKEN` (`xapp-`) | Signing secret `OPENPROGRAM_SLACK_SIGNING_SECRET` |
| Runs in | `worker` (`python -m infra.workflows.worker`) | `backend` API |

Both paths feed the same intake (`ServiceRegistry.accept_chat_event`), so
mapping, `event_id` redelivery dedup, reply coalescing and the inbound sweeper
behave identically. Use `socket` unless the deployment already has a public
ingress for other reasons.

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

## Socket Mode (`socket`, default)

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

## Events API over HTTP (`http`)

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
