PYTHONPATH ?= backend
PULSEOPS_SECRET_KEY ?= q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=
export PULSEOPS_SECRET_KEY

.PHONY: up down migrate seed test lint format api openapi worker frontend-install frontend-dev frontend-lint frontend-build

up:
	docker compose up -d

down:
	docker compose down

migrate:
	PYTHONPATH=$(PYTHONPATH) uv run alembic -c backend/infra/persistence/alembic.ini upgrade head

seed:
	PYTHONPATH=$(PYTHONPATH) uv run python -m infra.persistence.seed

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

worker:
	PYTHONPATH=$(PYTHONPATH) uv run python -m infra.workflows.worker

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-lint:
	cd frontend && npm run lint && npm run typecheck

frontend-build:
	cd frontend && npm run build
