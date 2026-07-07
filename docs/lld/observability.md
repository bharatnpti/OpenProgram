# Observability LLD

## Logging

`infra.observability.logging.configure_logging` configures structlog with correlation IDs. `redact_sensitive` strips focused raw-message event keys such as `raw_reply`, `dm_text`, and `reply_text` from application logs.

## Tracing

API middleware creates a correlation ID when the caller does not provide one. Application and adapter seams carry the ID through DTO metadata.

## Metrics

Prometheus scrapes the backend and infrastructure services. Grafana loads a starter dashboard from `infra/grafana/dashboards`.

## Conversation Payloads

Raw inbound and outbound conversation turns are persisted in the durable conversation store with configurable last-access-based retention. Application logs and generic OTEL spans must use safe metadata rather than raw conversation text. LLM prompts remain visible in Langfuse traces by design.
