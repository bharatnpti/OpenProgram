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

`docker-compose.yml` starts Postgres and the worker container. Worker startup resolves `Settings.workflow_provider` through `ServiceRegistry`, bootstraps schedules through the provider-neutral `WorkflowScheduler` port, then runs the selected `WorkflowWorker`. `dbos` is the default provider and stores workflow state in the configured Postgres database. Temporal and Temporal UI services remain available, and deployments can switch back with `OPENPROGRAM_WORKFLOW_PROVIDER=temporal`.

The shared heartbeat schedule id is configured with `OPENPROGRAM_HEARTBEAT_SCHEDULE_ID`. `OPENPROGRAM_TEMPORAL_SCHEDULE_ID` remains accepted as a legacy fallback when the generic setting is unset.

## Tests

Pure unit tests validate heartbeat output, provider schedule id selection, DBOS readiness launch caching, and fake scheduling. End-to-end worker tests target both DBOS/Postgres and Temporal adapters, including deterministic DBOS schedule triggering, and are gated on Docker availability.
