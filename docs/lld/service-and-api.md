# Service and API LLD

## App Factory

`api.main.create_app(settings: Settings | None = None)` builds FastAPI, configures logging, attaches the service registry, and registers routers.

## Routers

- `/health`: process liveness.
- `/ready`: settings and dependency readiness.
- `/graph/programs/{program_id}/tree`: sample graph read through `GraphRepository`.
- `/webhooks/chat/{provider}`: provider-neutral webhook intake route; `/webhooks/chat/slack` remains the Slack path.

## DTO Rule

API Pydantic models live in `api.dtos`. Domain dataclasses are never used as request/response models directly.

## OpenAPI

`python -m api.openapi` emits `frontend/src/api/openapi.json` for frontend generation.

## Tests

API tests use dependency-injected in-memory ports and FastAPI `TestClient`.
