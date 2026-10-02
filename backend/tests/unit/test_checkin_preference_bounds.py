from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.dtos import MAX_CHECKIN_WAIT_SECONDS, CheckinPreferenceUpdateRequest
from api.main import create_app
from config.settings import Settings


@pytest.mark.parametrize("seconds", [0, 60, 3600, 14400, 28800, MAX_CHECKIN_WAIT_SECONDS])
def test_reply_windows_accept_every_storable_value(seconds: int) -> None:
    request = CheckinPreferenceUpdateRequest(
        reply_wait_seconds=seconds,
        final_reply_wait_seconds=seconds,
    )

    assert request.reply_wait_seconds == seconds
    assert request.final_reply_wait_seconds == seconds


@pytest.mark.parametrize("seconds", [-1, MAX_CHECKIN_WAIT_SECONDS + 1])
@pytest.mark.parametrize("field", ["reply_wait_seconds", "final_reply_wait_seconds"])
def test_reply_windows_reject_values_that_cannot_be_stored(field: str, seconds: int) -> None:
    with pytest.raises(ValidationError):
        CheckinPreferenceUpdateRequest.model_validate({field: seconds})


def test_member_reply_windows_round_trip_and_survive_an_empty_save(settings: Settings) -> None:
    app = create_app(settings=settings)
    path = "/config/members/dev-ada/checkin-preference"
    with TestClient(app) as client:
        client.post("/config/members", json={"id": "dev-ada", "name": "Ada"})
        saved = client.put(
            path,
            json={"reply_wait_seconds": 14400, "final_reply_wait_seconds": 28800},
        )
        # The console leaves unchanged fields out, so a save with no edits is empty.
        empty_save = client.put(path, json={})
        too_long = client.put(path, json={"reply_wait_seconds": MAX_CHECKIN_WAIT_SECONDS + 1})
        listed = client.get("/config/checkin-preferences")

    assert saved.status_code == 200
    assert empty_save.status_code == 200
    assert empty_save.json()["reply_wait_seconds"] == 14400
    assert empty_save.json()["final_reply_wait_seconds"] == 28800
    assert too_long.status_code == 422
    stored = next(item for item in listed.json() if item["developer_id"] == "dev-ada")
    assert stored["reply_wait_seconds"] == 14400
    assert stored["final_reply_wait_seconds"] == 28800


def test_own_reply_window_rejects_values_that_cannot_be_stored(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-asha"}
        )
    )
    with TestClient(app) as client:
        response = client.put(
            "/me/checkin-preference",
            json={"final_reply_wait_seconds": MAX_CHECKIN_WAIT_SECONDS + 1},
        )

    assert response.status_code == 422
