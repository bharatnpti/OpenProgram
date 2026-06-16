from __future__ import annotations

from collections.abc import MutableMapping

import structlog

from infra.observability.tracing import current_correlation_id

SENSITIVE_KEYS = frozenset({"text", "message", "raw_body", "token", "secret", "authorization"})


def redact_sensitive(
    logger: object,
    method_name: str,
    event_dict: MutableMapping[str, object],
) -> MutableMapping[str, object]:
    for key in tuple(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def inject_correlation_id(
    logger: object,
    method_name: str,
    event_dict: MutableMapping[str, object],
) -> MutableMapping[str, object]:
    event_dict["correlation_id"] = current_correlation_id()
    return event_dict


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            redact_sensitive,
            inject_correlation_id,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=True,
    )
