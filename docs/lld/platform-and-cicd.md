# Platform and CI/CD LLD

## Local Platform

`docker-compose.yml` starts:

- Custom Postgres 16 image with AGE, TimescaleDB, and pgvector installed.
- Redis.
- Temporal, Temporal UI, worker, and scheduler bootstrap.
- LiteLLM plus a local OpenAI-compatible mock LLM.
- Langfuse v3 web/worker with ClickHouse, Redis, MinIO, and headless local project/API-key initialization.
- OpenTelemetry collector.
- Prometheus and Grafana.
- Backend API.

## Settings

`config.Settings` uses `pydantic-settings` with `PULSEOPS_` environment variables. Required secrets fail fast at boot.

## Developer Commands

- `make up`
- `make migrate`
- `make seed`
- `make schedule`
- `make smoke`
- `make integration`
- `make verify`
- `make test`
- `make lint`
- `make openapi`

## CI

CI runs ruff, format check, mypy strict, import-linter, tests with coverage on `backend/core`, Docker-backed integration tests, frontend lint/typecheck/build, image build, GHCR push on non-PR runs, and a dev deployment summary using the pushed image.
