# Scheduled Workflow LLD

## Classes

- `HeartbeatWorkflow`: durable workflow entrypoint.
- `record_heartbeat_activity`: idempotent activity that returns a stable heartbeat result.
- `run_worker`: Temporal worker process wiring.
- `ensure_heartbeat_schedule`: idempotent schedule bootstrap used by `make schedule` and the compose `scheduler` service.

## Idempotency

The activity accepts a deterministic `heartbeat_id`. Replaying the same input produces the same logical result and is safe for retry.

## Local Operation

`docker-compose.yml` starts Temporal, the UI, a worker container, and a one-shot scheduler container. The worker and scheduler connect to `Settings.temporal_target`.

## Tests

Pure unit tests validate activity output. End-to-end worker tests are integration tests gated on Docker/Temporal availability.
