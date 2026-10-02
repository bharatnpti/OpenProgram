# Service and API LLD

## App Factory

`api.main.create_app(settings: Settings | None = None)` builds FastAPI, configures logging, attaches the service registry, and registers routers.

## Routers

- `/health`: process liveness.
- `/ready`: settings and dependency readiness.
- `/graph/programs/{program_id}/tree`: sample graph read through `GraphRepository`.
- `/webhooks/chat/{provider}`: provider-neutral webhook intake route; `/webhooks/chat/slack` is the Slack path under the default `slack_inbound_transport=http`. With `socket`, Slack events arrive through the worker's Socket Mode listener instead, into the same `ServiceRegistry.accept_chat_event` intake.

## DTO Rule

API Pydantic models live in `api.dtos`. Domain dataclasses are never used as request/response models directly.

## OpenAPI

`python -m api.openapi` emits `frontend/src/api/openapi.json` for frontend generation.

## Tests

API tests use dependency-injected in-memory ports and FastAPI `TestClient`.
