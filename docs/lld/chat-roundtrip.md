# Chat Roundtrip LLD

## Flow

```mermaid
sequenceDiagram
    participant Chat as Chat Platform
    participant API as Webhook Router
    participant Adapter as Chat Adapter
    participant App as Application
    Chat->>API: inbound event
    API->>Adapter: map_webhook(payload)
    Adapter->>App: InboundMessage
    App->>Adapter: send_dm(user, OutboundMessage)
    Adapter->>Chat: provider API call
```

## Inbound transports

Real Slack events reach `ServiceRegistry.accept_chat_event` by one of two transports, chosen by `slack_inbound_transport`:

- `socket`: `SlackSocketModeListener` runs in the worker, opens a Socket Mode WebSocket with the app-level token (`apps.connections.open`), and passes each `events_api` envelope payload into the intake. It acks the envelope only after intake succeeds; an unacked envelope is redelivered by Slack.
- `http` (default): Slack POSTs to `/webhooks/chat/slack`; the router verifies the signature and calls the same intake.

The envelope payload is the same JSON body Slack would POST to the webhook, so both transports share `SlackChatWebhookMapper`, `event_id` dedup, reply coalescing and the inbound sweeper. Setup and operations: [docs/ops/slack-setup.md](../ops/slack-setup.md).

## Classes

- `SlackChatAdapter`: first real adapter, isolated under `infra.adapters.chat`.
- `ChatWebhookMapper`: inbound provider payload mapper port.
- `SlackChatWebhookMapper`: Slack-specific inbound mapper.
- `HttpSlackClient`: Slack Web API implementation with bounded retry handling.
- `SlackSocketModeListener`: Socket Mode intake loop with reconnect backoff and a Redis heartbeat (`RedisSocketHeartbeat`) that backs the `slack_socket` readiness probe.
- `RedisRateLimiter`: runtime rate-limit seam backed by the Redis container.
- `InMemoryRateLimiter`: deterministic rate-limit seam for unit and contract tests.

## Mapping

Inbound webhooks map to `InboundMessage`. Outbound text maps from `OutboundMessage`. Raw inbound and outbound conversation turns are retained in the durable conversation store according to configured retention.

## Tests

- Shared `ChatProvider` and `ChatWebhookMapper` contracts run against fakes and real adapters.
- Adapter tests use a fake HTTP client plus recorded-style `respx` Slack Web API fixtures for open DM, send, reply, retry, and provider failures.

## Local Manual E2E

Use the local chat simulator when a tester needs a Slack-shaped roundtrip without real Slack credentials.

1. Set `OPENPROGRAM_CHAT_PROVIDER=mock_slack`, `OPENPROGRAM_DIRECTORY_PROVIDER=mock_slack`, and `OPENPROGRAM_CHAT_SIMULATOR_ENABLED=true` in `.env`.
2. Start the stack and frontend, then sync the directory from Admin Config.
3. Add one or more synced users as members.
4. Open `/mock-slack`, dispatch a check-in, inspect the outbound bot DM, and submit a reply.
5. Verify the developer dashboard or pod check-in view shows the confirmed status.

The simulator state is in memory during `runtime_mode=memory` and Redis-backed in container mode so backend, worker, and scheduler processes share the same local mailbox.
