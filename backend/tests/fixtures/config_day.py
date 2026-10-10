"""The config API acting on one day, for tests that set up through it and read that day."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request

from api.dependencies import config_service_for, get_config_service, get_registry
from core.application.config_service import ConfigService


def config_api_on(app: FastAPI, day: date) -> FastAPI:
    """Have the app's config API make and end links on ``day``, not on the real today.

    A config link holds from the day it is made, so a test that sets up through
    the API and then reads a fixed ``as_of`` makes its links on that day.
    """

    def on_day(request: Request) -> ConfigService:
        return config_service_for(get_registry(request), today=lambda: day)

    app.dependency_overrides[get_config_service] = on_day
    return app
