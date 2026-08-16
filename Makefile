PYTHONPATH ?= backend
OPENPROGRAM_RUNTIME_MODE ?= container
OPENPROGRAM_SECRET_KEY ?= q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=
OPENPROGRAM_DATABASE_URL ?= postgresql://openprogram:openprogram@localhost:5432/openprogram
OPENPROGRAM_REDIS_URL ?= redis://localhost:6379/0
OPENPROGRAM_WORKFLOW_PROVIDER ?= dbos
OPENPROGRAM_DBOS_APP_NAME ?= openprogram
OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL ?= $(OPENPROGRAM_DATABASE_URL)
OPENPROGRAM_DBOS_HEARTBEAT_CRON ?= 0 * * * * *
OPENPROGRAM_TEMPORAL_SCHEDULE_ID ?=
OPENPROGRAM_HEARTBEAT_SCHEDULE_ID ?= $(if $(OPENPROGRAM_TEMPORAL_SCHEDULE_ID),$(OPENPROGRAM_TEMPORAL_SCHEDULE_ID),openprogram-heartbeat)
OPENPROGRAM_TEMPORAL_TARGET ?= localhost:7233
OPENPROGRAM_LITELLM_BASE_URL ?= http://localhost:4000
OPENPROGRAM_LITELLM_API_KEY ?= local-litellm-key
OPENPROGRAM_LITELLM_MODEL ?= local-gpt
OPENPROGRAM_LANGFUSE_HOST ?= http://localhost:3001
OPENPROGRAM_LANGFUSE_PUBLIC_KEY ?= pk-lf-local
OPENPROGRAM_LANGFUSE_SECRET_KEY ?= sk-lf-local
OPENPROGRAM_LANGFUSE_PROJECT_ID ?= local-project
export OPENPROGRAM_RUNTIME_MODE
export OPENPROGRAM_SECRET_KEY
export OPENPROGRAM_DATABASE_URL
export OPENPROGRAM_REDIS_URL
export OPENPROGRAM_WORKFLOW_PROVIDER
export OPENPROGRAM_DBOS_APP_NAME
export OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL
export OPENPROGRAM_DBOS_HEARTBEAT_CRON
export OPENPROGRAM_HEARTBEAT_SCHEDULE_ID
export OPENPROGRAM_TEMPORAL_TARGET
export OPENPROGRAM_LITELLM_BASE_URL
export OPENPROGRAM_LITELLM_API_KEY
export OPENPROGRAM_LITELLM_MODEL
export OPENPROGRAM_LANGFUSE_HOST
export OPENPROGRAM_LANGFUSE_PUBLIC_KEY
export OPENPROGRAM_LANGFUSE_SECRET_KEY
export OPENPROGRAM_LANGFUSE_PROJECT_ID

.PHONY: up down migrate smoke phase1-smoke integration ui-bdd verify test lint format api openapi openapi-check worker mock-llm frontend-install frontend-dev frontend-lint frontend-format frontend-build frontend-generate

up:
	docker compose up -d

down:
	docker compose down

migrate:
	PYTHONPATH=$(PYTHONPATH) uv run alembic -c backend/infra/persistence/alembic.ini upgrade head

smoke:
	PYTHONPATH=$(PYTHONPATH) uv run python -m infra.smoke

phase1-smoke:
	PYTHONPATH=$(PYTHONPATH) uv run python -m infra.phase1_smoke

integration:
	OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=$(PYTHONPATH) uv run pytest backend/tests/integration backend/tests/bdd -m integration --no-cov

# Playwright-backed Mock Slack BDD scenarios (MS-E2E-012/025/031-033/036/037/040/043).
# Requires `npm install` in frontend/ and `uv run playwright install chromium` once.
ui-bdd:
	OPENPROGRAM_RUN_UI_BDD=1 PYTHONPATH=$(PYTHONPATH) uv run pytest backend/tests/bdd -m ui_bdd_scenario --no-cov

verify: lint test frontend-lint frontend-build openapi-check integration smoke phase1-smoke

test:
	PYTHONPATH=$(PYTHONPATH) uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	PYTHONPATH=$(PYTHONPATH) uv run mypy
	PYTHONPATH=$(PYTHONPATH) uv run lint-imports

format:
	uv run ruff check --fix .
	uv run ruff format .

api:
	PYTHONPATH=$(PYTHONPATH) uv run uvicorn api.main:create_app --factory --reload --app-dir backend

openapi:
	PYTHONPATH=$(PYTHONPATH) uv run python -m api.openapi

openapi-check:
	$(MAKE) openapi
	cd frontend && npx prettier --write src/api/openapi.json
	cd frontend && npm run generate:client
	cp frontend/src/api/openapi.json frontend-v2/src/api/openapi.json
	cd frontend-v2 && npx prettier --write src/api/openapi.json
	cd frontend-v2 && npm run generate:client
	git diff --exit-code frontend/src/api/openapi.json frontend/src/api/generated.ts frontend-v2/src/api/openapi.json frontend-v2/src/api/generated.ts

worker:
	PYTHONPATH=$(PYTHONPATH) uv run python -m infra.workflows.worker

mock-llm:
	uv run uvicorn scripts.mock_llm:app --host 0.0.0.0 --port 8089

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-lint:
	cd frontend && npm run lint && npm run typecheck && npm run format:check

frontend-format:
	cd frontend && npm run format

frontend-build:
	cd frontend && npm run build

frontend-generate:
	cd frontend && npm run generate:client
