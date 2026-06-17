# Scheduled Workflow LLD

## Ports and Adapters

- `WorkflowScheduler`: provider-neutral schedule bootstrap port.
- `WorkflowWorker`: provider-neutral worker runtime port.
- `DbosWorkflowScheduler`: DBOS-backed schedule adapter using the existing Postgres system database.
- `DbosWorkflowWorker`: DBOS-backed worker adapter.
- `TemporalWorkflowScheduler`: Temporal-backed schedule adapter kept for deployments that select Temporal.
- `TemporalWorkflowWorker`: Temporal-backed worker adapter kept for deployments that select Temporal.
- `FakeWorkflowScheduler` / `FakeWorkflowWorker`: local and test adapters.
- `HeartbeatInput`, `HeartbeatResult`, and `ScheduleBootstrapResult`: provider-neutral DTOs in `core.domain.workflows`.

## Idempotency

The pure heartbeat function accepts a deterministic `heartbeat_id`. Provider adapters wrap it in their own workflow/step or workflow/activity runtime and keep retries idempotent at the logical result level.

## Local Operation

`docker-compose.yml` starts Postgres, the worker container, and a one-shot scheduler container. The worker and scheduler CLI entrypoints resolve `Settings.workflow_provider` through `ServiceRegistry`; `dbos` is the default provider and stores workflow state in the configured Postgres database. Temporal and Temporal UI services remain available, and deployments can switch back with `PULSEOPS_WORKFLOW_PROVIDER=temporal`.

## Tests

Pure unit tests validate heartbeat output and fake scheduling. End-to-end worker tests target both DBOS/Postgres and Temporal adapters and are gated on Docker availability.
