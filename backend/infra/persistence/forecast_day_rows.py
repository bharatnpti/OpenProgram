"""The forecast day rows a repository stores: one per key, whatever it was handed."""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from core.domain.forecast import ForecastDayRecord, forecast_day_key, last_row_per_key


def distinct_forecast_days(records: Sequence[ForecastDayRecord]) -> list[ForecastDayRecord]:
    """``records`` with one per key, the last winning, and a warning naming each key repeated.

    The key is (tenant, scope kind, scope id, project, day): a pod two projects
    share offers one row under each, which is two keys, not a repeat. Postgres
    refuses a multi-row ``INSERT ... ON CONFLICT DO UPDATE`` that offers one key
    twice (CardinalityViolation) and saves none of its rows, and the
    in-memory repository must keep the same row Postgres would, so both go through
    here. The repeat is a bug somewhere upstream: it is said, and it costs only
    itself.
    """
    rows, replaced = last_row_per_key(records)
    if not replaced:
        return rows
    logger = structlog.get_logger(__name__)
    for record in replaced:
        tenant_id, kind, scope_id, project_id, day = forecast_day_key(record)
        logger.warning(
            "forecast_day_duplicate_key",
            tenant_id=tenant_id,
            key=f"{kind}/{scope_id}/{day.isoformat()}",
            project_id=project_id,
        )
    return rows
