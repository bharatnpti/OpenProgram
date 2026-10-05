"""The escalation matrix: when each kind of ask goes up a level, and to whom."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.escalation_matrix_service import EscalationMatrixService, MatrixSource
from core.domain.errors import GraphNotFound
from core.domain.escalation_matrix import (
    TENANT_SCOPE,
    ContactSource,
    EscalationLevel,
    EscalationMatrix,
    EscalationMatrixError,
    NeedType,
    default_matrix,
    reached_levels,
    validated_matrix,
)
from core.domain.graph import Developer, Project
from infra.persistence.in_memory_escalation import InMemoryEscalationMatrixRepository
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_escalation import PostgresEscalationMatrixRepository

TENANT = "demo"
NOW = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)


def _matrix(*levels: EscalationLevel, project_id: str = "checkout") -> EscalationMatrix:
    return EscalationMatrix(
        tenant_id=TENANT, project_id=project_id, decision_owner_id=None, levels=levels
    )


# --- The matrix -----------------------------------------------------------------------------


def test_an_ask_reaches_each_level_once_it_has_waited_long_enough() -> None:
    matrix = default_matrix(TENANT)

    assert reached_levels(matrix, NeedType.FIX, 1) == ()
    assert [reached.number for reached in reached_levels(matrix, NeedType.FIX, 2)] == [2]
    assert [reached.level.label for reached in reached_levels(matrix, NeedType.ANSWER, 6)] == [
        "Scrum master",
        "Manager",
    ]


def test_a_kind_a_level_leaves_out_never_reaches_it() -> None:
    matrix = _matrix(
        EscalationLevel(
            label="Lead", source=ContactSource.TEAM_MANAGER, after_days={NeedType.FIX: 1}
        )
    )

    assert reached_levels(matrix, NeedType.REVIEW, 90) == ()


def test_a_matrix_is_tidied() -> None:
    checked = validated_matrix(
        EscalationMatrix(
            tenant_id=TENANT,
            project_id=" checkout ",
            decision_owner_id="  ",
            levels=(
                EscalationLevel(
                    label="  Delivery   lead ",
                    source=ContactSource.TEAM_SCRUM_MASTER,
                    member_id="ignored",
                    after_days={NeedType.REVIEW: 2, NeedType.FIX: 1},
                ),
            ),
        )
    )

    assert checked.project_id == "checkout"
    assert checked.decision_owner_id is None
    (level,) = checked.levels
    assert level.label == "Delivery lead"
    assert level.member_id is None
    assert list(level.after_days) == [NeedType.FIX, NeedType.REVIEW]


def test_a_matrix_that_cannot_work_names_every_problem() -> None:
    with pytest.raises(EscalationMatrixError) as raised:
        validated_matrix(
            _matrix(
                EscalationLevel(
                    label="Lead",
                    source=ContactSource.TEAM_SCRUM_MASTER,
                    after_days={NeedType.FIX: 5},
                ),
                EscalationLevel(label=" ", source=ContactSource.MEMBER, after_days={}),
                EscalationLevel(
                    label="Head",
                    source=ContactSource.TEAM_MANAGER,
                    after_days={NeedType.FIX: 3, NeedType.ANSWER: 400},
                ),
            )
        )

    assert str(raised.value) == (
        "Level 3 needs a name; level 3 needs the member it goes to; level 4 waits between 0 "
        "and 365 days; level 4 is reached before level 2 for fix."
    )


# --- The service ----------------------------------------------------------------------------


async def _service() -> EscalationMatrixService:
    store = InMemoryGraphStore()
    await store.upsert_node(Project(tenant_id=TENANT, id="checkout", name="Checkout"))
    await store.upsert_node(Developer(tenant_id=TENANT, id="dev-eli", name="Eli"))
    return EscalationMatrixService(
        repository=InMemoryEscalationMatrixRepository(),
        graph_repository=store,
        clock=lambda: NOW,
    )


async def test_a_project_uses_its_own_matrix_else_the_tenants_else_the_default() -> None:
    service = await _service()
    lead = EscalationLevel(
        label="Delivery lead",
        source=ContactSource.MEMBER,
        member_id="dev-eli",
        after_days={NeedType.FIX: 1},
    )

    default = await service.matrix_for(TENANT, "checkout")
    await service.save(_matrix(lead, project_id=TENANT_SCOPE), actor="admin")
    inherited = await service.matrix_for(TENANT, "checkout")
    own = await service.save(_matrix(lead), actor="admin")
    mine = await service.matrix_for(TENANT, "checkout")
    await service.remove(TENANT, "checkout")
    back = await service.matrix_for(TENANT, "checkout")

    assert default.source is MatrixSource.DEFAULT
    assert (inherited.source, inherited.matrix.project_id) == (MatrixSource.TENANT, "checkout")
    assert (own.updated_at, own.updated_by) == (NOW, "admin")
    assert mine.source is MatrixSource.PROJECT
    assert back.source is MatrixSource.TENANT


async def test_saving_needs_a_real_project_and_real_members() -> None:
    service = await _service()

    with pytest.raises(GraphNotFound):
        await service.save(_matrix(project_id="nope"), actor="admin")
    with pytest.raises(EscalationMatrixError, match="No member 'dev-zed'"):
        await service.save(
            EscalationMatrix(
                tenant_id=TENANT, project_id="checkout", decision_owner_id="dev-zed", levels=()
            ),
            actor="admin",
        )


# --- Postgres -------------------------------------------------------------------------------


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]] = field(default_factory=list)
    calls: list[tuple[str, Sequence[object]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, params))
        return object()

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, params))
        return self.rows


async def test_postgres_writes_and_reads_a_matrix_back() -> None:
    saved = EscalationMatrix(
        tenant_id=TENANT,
        project_id="checkout",
        decision_owner_id="dev-eli",
        levels=default_matrix(TENANT).levels,
        updated_at=NOW,
        updated_by="admin",
    )
    executor = _RecordingExecutor()
    repository = PostgresEscalationMatrixRepository(executor)

    await repository.save(saved)
    _query, params = executor.calls[0]
    executor.rows = [
        {
            "tenant_id": TENANT,
            "project_id": "checkout",
            "decision_owner_id": "dev-eli",
            "levels": params[3],
            "updated_at": NOW,
            "updated_by": "admin",
        }
    ]
    read = await repository.get(TENANT, "checkout")

    assert read == saved


# --- API ------------------------------------------------------------------------------------


def _level_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "label": "Delivery lead",
        "source": "team_manager",
        "after_days": {"fix": 1, "decision": 1},
    }
    body.update(overrides)
    return body


def test_api_sets_the_tenants_matrix_and_a_projects_own(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        before = client.get("/config/escalation")
        tenant = client.put("/config/escalation/tenant", json={"levels": [_level_body()]})
        inherited = client.get("/config/escalation/projects/checkout")
        own = client.put(
            "/config/escalation/projects/checkout",
            json={"levels": [_level_body(label="Head of delivery")]},
        )
        after = client.get("/config/escalation")
        removed = client.delete("/config/escalation/projects/checkout")
        back = client.get("/config/escalation/projects/checkout")
        unknown = client.put("/config/escalation/projects/nope", json={"levels": []})
        invalid = client.put(
            "/config/escalation/tenant", json={"levels": [_level_body(source="member")]}
        )

    assert before.json()["tenant"]["source"] == "default"
    assert len(before.json()["tenant"]["levels"]) == 2
    assert tenant.json()["source"] == "tenant"
    assert inherited.json()["source"] == "tenant"
    assert own.json()["source"] == "project"
    assert [item["project_id"] for item in after.json()["projects"]] == ["checkout"]
    assert removed.status_code == 204
    assert back.json()["levels"][0]["label"] == "Delivery lead"
    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert "needs the member it goes to" in invalid.json()["detail"]


@pytest.mark.parametrize("role", ["dev", "po", "sm", "mgr", "exec"])
def test_only_an_admin_sets_escalation(settings: Settings, role: str) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [
            client.get("/config/escalation"),
            client.put("/config/escalation/tenant", json={"levels": []}),
            client.delete("/config/escalation/projects/checkout"),
        ]

    assert [response.status_code for response in responses] == [403] * 3
