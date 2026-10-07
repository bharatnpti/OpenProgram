"""The own-status routes: a correction names the blockers it restates by id."""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import Developer
from core.domain.status import DeveloperStatus, StatusSource

DAY = date(2026, 6, 15)
MEMBER = "dev-1"


def _app(settings: Settings, subject: str = MEMBER) -> FastAPI:
    return create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": subject}
        )
    )


def _blocker(blocker_id: str, description: str, first_seen_on: date) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=blocker_id,
        developer_id=MEMBER,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id="PROJ-1",
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen_on,
        last_seen_on=DAY,
    )


async def _member(app: FastAPI, *, blockers: tuple[DeveloperBlocker, ...] = ()) -> None:
    registry = app.state.registry
    await registry.graph_repository().upsert_node(
        Developer(tenant_id="demo", id=MEMBER, name="Asha")
    )
    if blockers:
        await registry.status_repository().record_developer_status_with_blockers(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=MEMBER,
                as_of=DAY,
                source=StatusSource.CONFIRMED,
                blockers=tuple(blocker.description for blocker in blockers),
                summary="Checkout form in progress.",
            ),
            blockers,
        )


def test_a_correction_keeps_the_blocker_its_id_names_on_a_shared_work_item(
    settings: Settings,
) -> None:
    review = _blocker("b-review", "waiting on code review", date(2026, 6, 8))
    staging = _blocker("b-staging", "staging is down", date(2026, 6, 12))
    app = _app(settings)
    with TestClient(app) as client:
        asyncio.run(_member(app, blockers=(review, staging)))
        response = client.post(
            f"/me/status/correct?as_of={DAY.isoformat()}",
            json={
                "summary": "Checkout form in progress.",
                "blocker_items": [
                    {
                        "blocker_id": "b-staging",
                        "description": "staging is down",
                        "work_item_id": "PROJ-1",
                    }
                ],
            },
        )
        still_open = asyncio.run(
            app.state.registry.status_repository().open_blockers("demo", MEMBER, DAY)
        )

    assert response.status_code == 200, response.text
    # The id reached the matcher: the staging blocker is kept at its own age
    # and the review blocker, left out, is the one resolved.
    assert [(item.blocker_id, item.first_seen_on) for item in still_open] == [
        ("b-staging", date(2026, 6, 12))
    ]
    details = response.json()["blocker_details"]
    assert [(item["blocker_id"], item["age_days"]) for item in details] == [("b-staging", 3)]
