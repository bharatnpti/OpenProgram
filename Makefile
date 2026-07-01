PYTHONPATH ?= backend
PULSEOPS_RUNTIME_MODE ?= container
PULSEOPS_SECRET_KEY ?= q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=
PULSEOPS_DATABASE_URL ?= postgresql://pulseops:pulseops@localhost:5432/pulseops
PULSEOPS_REDIS_URL ?= redis://localhost:6379/0
PULSEOPS_WORKFLOW_PROVIDER ?= dbos
PULSEOPS_DBOS_APP_NAME ?= pulseops
PULSEOPS_DBOS_SYSTEM_DATABASE_URL ?= $(PULSEOPS_DATABASE_URL)
PULSEOPS_DBOS_HEARTBEAT_CRON ?= 0 * * * * *
PULSEOPS_TEMPORAL_SCHEDULE_ID ?=
PULSEOPS_HEARTBEAT_SCHEDULE_ID ?= $(if $(PULSEOPS_TEMPORAL_SCHEDULE_ID),$(PULSEOPS_TEMPORAL_SCHEDULE_ID),pulseops-heartbeat)
PULSEOPS_TEMPORAL_TARGET ?= localhost:7233
PULSEOPS_LITELLM_BASE_URL ?= http://localhost:4000
PULSEOPS_LITELLM_API_KEY ?= local-litellm-key
PULSEOPS_LITELLM_MODEL ?= local-gpt
PULSEOPS_LANGFUSE_HOST ?= http://localhost:3001
PULSEOPS_LANGFUSE_PUBLIC_KEY ?= pk-lf-local
PULSEOPS_LANGFUSE_SECRET_KEY ?= sk-lf-local
PULSEOPS_LANGFUSE_PROJECT_ID ?= local-project
export PULSEOPS_RUNTIME_MODE
export PULSEOPS_SECRET_KEY
export PULSEOPS_DATABASE_URL
export PULSEOPS_REDIS_URL
export PULSEOPS_WORKFLOW_PROVIDER
export PULSEOPS_DBOS_APP_NAME
export PULSEOPS_DBOS_SYSTEM_DATABASE_URL
export PULSEOPS_DBOS_HEARTBEAT_CRON
export PULSEOPS_HEARTBEAT_SCHEDULE_ID
export PULSEOPS_TEMPORAL_TARGET
export PULSEOPS_LITELLM_BASE_URL
export PULSEOPS_LITELLM_API_KEY
export PULSEOPS_LITELLM_MODEL
export PULSEOPS_LANGFUSE_HOST
export PULSEOPS_LANGFUSE_PUBLIC_KEY
export PULSEOPS_LANGFUSE_SECRET_KEY
export PULSEOPS_LANGFUSE_PROJECT_ID

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
	PULSEOPS_RUN_INTEGRATION=1 PYTHONPATH=$(PYTHONPATH) uv run pytest backend/tests/integration backend/tests/bdd -m integration --no-cov

# Playwright-backed Mock Slack BDD scenarios (MS-E2E-012/025/031-033/036/037/040/043).
# Requires `npm install` in frontend/ and `uv run playwright install chromium` once.
ui-bdd:
	PULSEOPS_RUN_UI_BDD=1 PYTHONPATH=$(PYTHONPATH) uv run pytest backend/tests/bdd -m ui_bdd_scenario --no-cov

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
	git diff --exit-code frontend/src/api/openapi.json frontend/src/api/generated.ts

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
