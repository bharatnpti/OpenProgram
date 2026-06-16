from __future__ import annotations

from collections.abc import MutableMapping

import structlog

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


def configure_logging() -> None:
    structlog.configure(
        processors=[
            redact_sensitive,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=True,
    )
