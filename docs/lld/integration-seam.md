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

All DTOs live in `core.domain` and carry `tenant_id` seams. Vendor payloads stay in `infra`.

## Composition Root

`infra.registry.ServiceRegistry` wires configured providers from `config.Settings` using constructor injection. Application code receives ports, never concrete adapters.

## Secret Storage

`SecretStore` is keyed by `(tenant_id, connector, key)`. `FernetSecretStore` encrypts values before storing them through a small persistence Protocol.

## Contract Tests

Each port has a reusable async contract test helper. The helper accepts a provider factory so fakes and real adapters run the same assertions. Fakes live in `backend/tests/contract/fakes.py`.

## Anti-Corruption Rule

Adapters translate external payloads to domain DTOs at the boundary. Provider-specific names may appear only under `infra/adapters/*`.
