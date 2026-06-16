# Observability LLD

## Logging

`infra.observability.logging.configure_logging` configures structlog with correlation IDs and a redaction processor. Keys such as `text`, `message`, `raw_body`, `token`, and `secret` are redacted.

## Tracing

API middleware creates a correlation ID when the caller does not provide one. Application and adapter seams carry the ID through DTO metadata.

## Metrics

Prometheus scrapes the backend and infrastructure services. Grafana loads a starter dashboard from `infra/grafana/dashboards`.

## PII Rule

Raw DM content and secrets must not appear in normal logs. Tests cover redaction.
