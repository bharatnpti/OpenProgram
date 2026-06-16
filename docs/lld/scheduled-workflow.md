# Scheduled Workflow LLD

## Classes

- `HeartbeatWorkflow`: durable workflow entrypoint.
- `record_heartbeat_activity`: idempotent activity that returns a stable heartbeat result.
- `run_worker`: Temporal worker process wiring.

## Idempotency

The activity accepts a deterministic `heartbeat_id`. Replaying the same input produces the same logical result and is safe for retry.

## Local Operation

`docker-compose.yml` starts Temporal and the UI. The worker connects to `Settings.temporal_target`.

## Tests

Pure unit tests validate idempotent activity output. End-to-end worker tests are integration tests gated on Temporal availability.
