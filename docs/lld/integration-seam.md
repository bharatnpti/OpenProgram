# Integration Seam LLD

## Ports

The core exposes one Protocol per external capability:

- `ChatProvider`
- `IssueTracker`
- `VcsProvider`
- `CiProvider`
- `CalendarProvider`
- `LlmProvider`
- `SecretStore`
- `ChatWebhookMapper`
- `WorkflowScheduler`
- `WorkflowWorker`

All DTOs live in `core.domain` and carry `tenant_id` seams. Vendor payloads stay in `infra`.

## Composition Root

`infra.registry.ServiceRegistry` delegates provider selection to the adapter catalog and returns ports using constructor injection. Application and API code receive provider-neutral ports, never concrete adapters. Current selectors are:

- `chat_provider`: `slack` or `fake`
- `llm_provider`: `litellm` or `fake`
- `workflow_provider`: `dbos`, `temporal`, or `fake`

## Secret Storage

`SecretStore` is keyed by `(tenant_id, connector, key)`. `FernetSecretStore` encrypts values before storing them through a small persistence Protocol.

## Contract Tests

Each port has a reusable async contract test helper. The helper accepts a provider factory so fakes and real adapters run the same assertions. Fakes live in `backend/tests/contract/fakes.py`.

## Anti-Corruption Rule

Adapters translate external payloads to domain DTOs at the boundary. Provider-specific SDK imports stay under `infra/adapters/*`, while config and docs may name provider IDs used for selection.
