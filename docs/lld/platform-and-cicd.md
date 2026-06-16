# Platform and CI/CD LLD

## Local Platform

`docker-compose.yml` starts:

- Postgres with extension initialization for AGE, TimescaleDB, and pgvector when the image supports them.
- Redis.
- Temporal and Temporal UI.
- Langfuse dependencies and service placeholders.
- OpenTelemetry collector.
- Prometheus and Grafana.
- Backend API.

## Settings

`config.Settings` uses `pydantic-settings` with `PULSEOPS_` environment variables. Required secrets fail fast at boot.

## Developer Commands

- `make up`
- `make migrate`
- `make seed`
- `make test`
- `make lint`
- `make openapi`

## CI

CI runs ruff, format check, mypy strict, import-linter, tests with coverage on `backend/core`, frontend lint/typecheck/build, image build, and a dev deploy placeholder.
