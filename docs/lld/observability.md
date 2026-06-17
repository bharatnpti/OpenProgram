# Observability LLD

## Logging

`infra.observability.logging.configure_logging` configures structlog with correlation IDs. The legacy `redact_sensitive` processor remains in the processor chain as a compatibility no-op and does not remove message or token fields.

## Tracing

API middleware creates a correlation ID when the caller does not provide one. Application and adapter seams carry the ID through DTO metadata.

## Metrics

Prometheus scrapes the backend and infrastructure services. Grafana loads a starter dashboard from `infra/grafana/dashboards`.

## Conversation Payloads

Raw inbound and outbound conversation turns are persisted in the durable conversation store with configurable retention. Logs and LLM traces retain submitted payload fields unless a caller explicitly omits them before logging.
