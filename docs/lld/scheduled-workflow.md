# Scheduled Workflow LLD

## Ports and Adapters

- `WorkflowScheduler`: provider-neutral schedule bootstrap port.
- `WorkflowWorker`: provider-neutral worker runtime port.
- `TemporalWorkflowScheduler`: Temporal-backed schedule adapter.
- `TemporalWorkflowWorker`: Temporal-backed worker adapter.
- `FakeWorkflowScheduler` / `FakeWorkflowWorker`: local and test adapters.
- `HeartbeatInput`, `HeartbeatResult`, and `ScheduleBootstrapResult`: provider-neutral DTOs in `core.domain.workflows`.

## Idempotency

The pure heartbeat function accepts a deterministic `heartbeat_id`. Provider adapters wrap it in their own workflow/activity runtime and keep retries idempotent at the logical result level.

## Local Operation

`docker-compose.yml` starts Temporal, the UI, a worker container, and a one-shot scheduler container. The worker and scheduler CLI entrypoints resolve `Settings.workflow_provider` through `ServiceRegistry`; `temporal` is the default provider.

## Tests

Pure unit tests validate heartbeat output and fake scheduling. End-to-end worker tests target the Temporal adapter and are gated on Docker/Temporal availability.
