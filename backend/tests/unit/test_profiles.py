"""Profiles per pod membership: the catalog, the suggestion, who sets them, and the dated rows.

Commerce's Payments Pod holds Ira (scrum master, its contact), Kai (developer)
and Sofia (developer, titled QA Engineer); the Storefront Pod holds Zoe. Ira
runs Payments and not Storefront. A profile grants no rights: these tests only
check who may set one and what each read returns.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_ask_service
from api.main import create_app
from config.settings import Settings
from core.application.ask_service import TeamProfilesTool
from core.application.day_report_asks import AskScope, owner_heading, profile_holder, team_from
from core.application.day_report_builder import _readiness_asks
from core.application.profile_service import ProfileService
from core.application.release_readiness_service import ReadinessGap, ReportGaps
from core.domain.auth import Principal, Role
from core.domain.directory import DirectoryUser
from core.domain.escalation_matrix import NeedType, default_matrix
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Project
from core.domain.profiles import (
    PodMemberProfile,
    ProfileConflict,
    ProfileDefinition,
    ProfileError,
    ProfileSource,
    SuggestionInputs,
    current_profile,
    default_profiles,
    merged_catalog,
    profile_for_pod_role,
    suggest_profile,
    validated_entry,
)
from core.domain.release_readiness import ScopeKind, ScopeRef, UrgencyKind
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.in_memory_profiles import InMemoryProfileRepository
from tests.unit.test_readiness_api import CRITERION

TENANT = "demo"
CATALOG = default_profiles()


def _as(role: str, user: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


# ---- the domain ------------------------------------------------------------------------------


def test_pod_roles_default_to_their_profile_and_anything_else_to_development() -> None:
    assert profile_for_pod_role("developer") == "development"
    assert profile_for_pod_role("product_owner") == "product"
    assert profile_for_pod_role("Scrum master") == "delivery"
    assert profile_for_pod_role("manager") == "management"
    assert profile_for_pod_role("tester") == "development"
    assert profile_for_pod_role(None) == "development"


def test_the_catalog_keeps_the_defaults_under_a_tenants_changes() -> None:
    merged = merged_catalog(
        [
            ProfileDefinition(slug="qa", label="Quality", code_work=False, sort=20),
            ProfileDefinition(slug="compliance", label="Compliance", code_work=False, sort=35),
        ]
    )
    by_slug = {item.slug: item for item in merged}

    assert by_slug["qa"].label == "Quality"
    assert by_slug["qa"].customized is True
    # The shipped suggestion inputs stay with a renamed entry.
    assert "bug" in by_slug["qa"].issue_types
    assert by_slug["compliance"].customized is True
    assert by_slug["development"].customized is False
    assert [item.slug for item in merged][:4] == ["development", "qa", "security", "compliance"]


def test_a_catalog_entry_is_refused_in_one_sentence() -> None:
    with pytest.raises(ProfileError, match="lower-case letters"):
        validated_entry(ProfileDefinition(slug="Not OK", label="x", code_work=False))
    with pytest.raises(ProfileError, match="name of 1 to 40"):
        validated_entry(ProfileDefinition(slug="ok", label="  ", code_work=False))
    tidy = validated_entry(
        ProfileDefinition(
            slug="ok", label="  Site  reliability ", code_work=True, title_words=("SRE", "sre ")
        )
    )
    assert tidy.label == "Site reliability"
    assert tidy.title_words == ("sre",)


@pytest.mark.parametrize(
    ("title", "profile"),
    [
        ("QA Engineer", "qa"),
        ("Senior Backend Engineer", "development"),
        ("Engineering Manager", "management"),
        ("Program Manager", "delivery"),
        ("Scrum Master", "delivery"),
        ("Product Owner", "product"),
        ("Business Analyst", "product"),
        ("Data Engineer", "data"),
        ("SRE", "ops"),
        ("Application Security Engineer", "security"),
        ("UX Designer", "design"),
        ("Director of Engineering", "management"),
    ],
)
def test_a_title_suggests_its_profile_with_the_reason(title: str, profile: str) -> None:
    suggestion = suggest_profile(CATALOG, SuggestionInputs(title=title, pod_role="developer"))
    assert suggestion.profile == profile
    assert suggestion.reasons[0] == f"their title says '{title}'"


def test_issues_and_git_suggest_when_the_title_does_not() -> None:
    bugs = suggest_profile(
        CATALOG, SuggestionInputs(issue_types=["Bug", "Bug", "Story"], pod_role="developer")
    )
    git = suggest_profile(CATALOG, SuggestionInputs(git_events=4, pod_role="manager"))
    labelled = suggest_profile(
        CATALOG,
        SuggestionInputs(issue_labels=[["security-review"], []], pod_role="developer"),
    )
    nothing = suggest_profile(CATALOG, SuggestionInputs(pod_role="product_owner"))

    assert bugs.profile == "qa"
    assert bugs.reasons == ("2 of their 3 issues in this pod are Bug",)
    assert git.profile == "development"
    assert git.reasons == ("4 commits or merge requests of theirs in the last 90 days",)
    assert labelled.profile == "security"
    assert nothing.profile == "product"
    assert nothing.reasons == ("from their role in the pod (product owner)",)


def test_agreeing_evidence_joins_the_reasons_and_hidden_profiles_are_never_suggested() -> None:
    agreeing = suggest_profile(
        CATALOG, SuggestionInputs(title="Backend Engineer", git_events=1, pod_role="developer")
    )
    hidden = merged_catalog(
        [ProfileDefinition(slug="qa", label="QA and test", code_work=False, sort=20, hidden=True)]
    )
    not_qa = suggest_profile(hidden, SuggestionInputs(title="QA Engineer", pod_role="developer"))

    assert agreeing.reasons == (
        "their title says 'Backend Engineer'",
        "1 commit or merge request of theirs in the last 90 days",
    )
    assert not_qa.profile == "development"


def test_the_current_profile_is_the_latest_row_on_or_before_the_day() -> None:
    def row(day: date, profile: str) -> PodMemberProfile:
        return PodMemberProfile(
            tenant_id=TENANT,
            pod_id="pod",
            member_id="m",
            profile=profile,
            valid_from=day,
            source=ProfileSource.ADMIN,
        )

    rows = [row(date(2026, 10, 1), "development"), row(date(2026, 10, 8), "qa")]
    assert current_profile(rows, date(2026, 9, 30)) is None
    assert current_profile(rows, date(2026, 10, 7)).profile == "development"  # type: ignore[union-attr]
    assert current_profile(rows, date(2026, 10, 8)).profile == "qa"  # type: ignore[union-attr]


# ---- the service -----------------------------------------------------------------------------


def _edge(parent: str, child: str, start: date | None = None) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT,
        from_node_id=parent,
        to_node_id=child,
        kind=EdgeKind.CONTAINS,
        valid_from=start,
        metadata={"role": "developer"},
    )


async def _service(today: list[date]) -> tuple[ProfileService, InMemoryGraphStore]:
    graph = InMemoryGraphStore()
    for node in (
        Pod(tenant_id=TENANT, id="pod-pay", name="Payments Pod"),
        Developer(tenant_id=TENANT, id="U-KAI", name="Kai"),
    ):
        await graph.upsert_node(node)
    await graph.add_edge(_edge("pod-pay", "U-KAI", date(2026, 9, 1)))
    service = ProfileService(
        repository=InMemoryProfileRepository(),
        graph_repository=graph,
        today=lambda: today[0],
        clock=lambda: datetime.combine(today[0], datetime.min.time(), tzinfo=UTC),
    )
    return service, graph


def test_a_change_holds_from_its_day_and_earlier_days_keep_the_old_profile() -> None:
    async def run() -> None:
        today = [date(2026, 10, 5)]
        service, _graph = await _service(today)
        await service.set_profile(
            TENANT, "pod-pay", "U-KAI", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
        )
        today[0] = date(2026, 10, 9)
        await service.set_profile(
            TENANT, "pod-pay", "U-KAI", "security", source=ProfileSource.ADMIN, by="U-ADMIN"
        )

        before = await service.pod_profiles(TENANT, "pod-pay", date(2026, 10, 4))
        first = await service.pod_profiles(TENANT, "pod-pay", date(2026, 10, 6))
        now = await service.pod_profiles(TENANT, "pod-pay", date(2026, 10, 9))

        assert before.members[0].profile == "development"
        assert before.members[0].source is None
        assert first.members[0].profile == "qa"
        assert now.members[0].profile == "security"
        assert now.members[0].checked is True

    asyncio.run(run())


def test_someone_added_back_starts_again_from_the_pod_role() -> None:
    async def run() -> None:
        today = [date(2026, 10, 5)]
        service, graph = await _service(today)
        await service.set_profile(
            TENANT, "pod-pay", "U-KAI", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
        )
        edge = (await graph.list_edges(TENANT, from_node_id="pod-pay", to_node_id="U-KAI"))[0]
        await graph.end_edge(edge, date(2026, 10, 6))
        await graph.add_edge(_edge("pod-pay", "U-KAI", date(2026, 10, 7)))
        today[0] = date(2026, 10, 7)

        view = await service.pod_profiles(TENANT, "pod-pay", date(2026, 10, 7))

        assert view.members[0].profile == "development"
        assert view.members[0].source is None

    asyncio.run(run())


def test_a_profile_needs_the_membership_and_an_offered_entry() -> None:
    async def run() -> None:
        service, graph = await _service([date(2026, 10, 5)])
        await graph.upsert_node(Developer(tenant_id=TENANT, id="U-ZOE", name="Zoe"))
        with pytest.raises(Exception, match="add them to the pod first"):
            await service.set_profile(
                TENANT, "pod-pay", "U-ZOE", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
            )
        with pytest.raises(ProfileError, match="There is no profile"):
            await service.set_profile(
                TENANT, "pod-pay", "U-KAI", "astronaut", source=ProfileSource.ADMIN, by="U-ADMIN"
            )
        with pytest.raises(ProfileError, match="Only the migration"):
            await service.set_profile(
                TENANT, "pod-pay", "U-KAI", "qa", source=ProfileSource.BACKFILL, by="U-ADMIN"
            )

    asyncio.run(run())


def test_only_a_checked_profile_decides_the_code_work_test() -> None:
    async def run() -> None:
        service, _graph = await _service([date(2026, 10, 5)])
        unchecked = await service.code_work_by_pod(TENANT, "U-KAI", date(2026, 10, 5))
        await service.set_profile(
            TENANT, "pod-pay", "U-KAI", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
        )
        checked = await service.code_work_by_pod(TENANT, "U-KAI", date(2026, 10, 5))

        assert unchecked == {}
        assert checked == {"pod-pay": False}

    asyncio.run(run())


# ---- the routes ------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(
        settings=settings.model_copy(
            update={"demo_mode": True, "issue_tracker_provider": "fake", "tenant_id": TENANT}
        )
    )
    with TestClient(app) as test_client:
        registry = app.state.registry
        graph = registry.graph_repository()

        async def seed() -> None:
            for node in (
                Pod(
                    tenant_id=TENANT,
                    id="pod-pay",
                    name="Payments Pod",
                    metadata={"escalation_sm_member_id": "U-IRA"},
                ),
                Pod(tenant_id=TENANT, id="pod-store", name="Storefront Pod"),
                Developer(tenant_id=TENANT, id="U-IRA", name="Ira Novak"),
                Developer(tenant_id=TENANT, id="U-KAI", name="Kai Thompson"),
                Developer(tenant_id=TENANT, id="U-SOFIA", name="Sofia Bergmann"),
                Developer(tenant_id=TENANT, id="U-ZOE", name="Zoe Almeida"),
            ):
                await graph.upsert_node(node)
            for pod, member, role in (
                ("pod-pay", "U-IRA", "scrum_master"),
                ("pod-pay", "U-KAI", "developer"),
                ("pod-store", "U-ZOE", "developer"),
            ):
                await graph.add_edge(
                    GraphEdge(
                        tenant_id=TENANT,
                        from_node_id=pod,
                        to_node_id=member,
                        kind=EdgeKind.CONTAINS,
                        valid_from=date.today() - timedelta(days=30),
                        metadata={"role": role},
                    )
                )
            await registry.directory_user_repository().upsert_users(
                [
                    DirectoryUser(
                        tenant_id=TENANT,
                        external_id=user_id,
                        display_name=name,
                        title=title,
                        source="slack",
                        synced_at=datetime.now(tz=UTC),
                    )
                    for user_id, name, title in (
                        ("U-SOFIA", "Sofia Bergmann", "QA Engineer"),
                        ("U-NEW", "Noor Haddad", "Security Engineer"),
                        ("U-LATE", "Lee Park", "UX Designer"),
                    )
                ]
            )

        asyncio.run(seed())
        yield test_client


def test_everyone_reads_the_catalog_and_only_an_admin_changes_it(client: TestClient) -> None:
    listed = client.get("/profiles", headers=_as("dev", "U-KAI"))
    refused = client.put(
        "/config/profiles/qa", json={"label": "Quality"}, headers=_as("sm", "U-IRA")
    )
    renamed = client.put("/config/profiles/qa", json={"label": "Quality engineering"})
    bad = client.put("/config/profiles/Bad%20Slug", json={"label": "x"})

    assert listed.status_code == 200
    assert [item["slug"] for item in listed.json()][:3] == ["development", "qa", "security"]
    assert refused.status_code == 403
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["label"] == "Quality engineering"
    assert renamed.json()["customized"] is True
    assert bad.status_code == 422
    assert "lower-case" in bad.json()["detail"]


def test_an_admin_adds_a_member_with_a_profile_and_a_refused_one_adds_nobody(
    client: TestClient,
) -> None:
    added = client.post(
        "/config/pods/pod-pay/members/U-SOFIA", json={"role": "developer", "profile": "qa"}
    )
    refused = client.post(
        "/config/pods/pod-store/members/U-SOFIA",
        json={"role": "developer", "profile": "astronaut"},
    )
    pay = client.get("/pods/pod-pay/profiles").json()
    store = client.get("/pods/pod-store/profiles").json()

    assert added.status_code == 200, added.text
    assert refused.status_code == 422
    assert "There is no profile 'astronaut'" in refused.json()["detail"]
    sofia = next(item for item in pay["members"] if item["member_id"] == "U-SOFIA")
    assert sofia["profile"] == "qa"
    assert sofia["label"] == "QA and test"
    assert sofia["source"] == "admin"
    assert sofia["checked"] is True
    kai = next(item for item in pay["members"] if item["member_id"] == "U-KAI")
    assert (kai["profile"], kai["source"], kai["checked"]) == ("development", "pod_role", False)
    ira = next(item for item in pay["members"] if item["member_id"] == "U-IRA")
    assert ira["profile"] == "delivery"
    assert pay["can_edit"] is True
    assert [item["member_id"] for item in store["members"]] == ["U-ZOE"]


def test_a_scrum_master_sets_profiles_only_in_the_pods_they_run(client: TestClient) -> None:
    ira = _as("sm", "U-IRA")
    own = client.put(
        "/config/pods/pod-pay/members/U-KAI/profile", json={"profile": "ops"}, headers=ira
    )
    other = client.put(
        "/config/pods/pod-store/members/U-ZOE/profile", json={"profile": "qa"}, headers=ira
    )
    developer = client.put(
        "/config/pods/pod-pay/members/U-KAI/profile",
        json={"profile": "qa"},
        headers=_as("dev", "U-KAI"),
    )
    manager = client.put(
        "/config/pods/pod-pay/members/U-KAI/profile",
        json={"profile": "qa"},
        headers=_as("mgr", "U-MGR"),
    )
    read_own = client.get("/pods/pod-pay/profiles", headers=ira).json()
    read_other = client.get("/pods/pod-store/profiles", headers=ira).json()

    assert own.status_code == 200, own.text
    assert own.json()["profile"] == "ops"
    assert own.json()["set_by_name"] == "Ira Novak"
    assert other.status_code == 403
    assert other.json()["detail"] == (
        "You set profiles in the pods you run, and this is not one of them."
    )
    assert developer.status_code == 403
    assert developer.json()["detail"] == (
        "Only an admin, or a scrum master in a pod they run, sets profiles."
    )
    assert manager.status_code == 403
    assert read_own["can_edit"] is True
    assert read_other["can_edit"] is False


def test_the_membership_read_suggests_with_reasons_even_before_they_join(
    client: TestClient,
) -> None:
    before = client.get("/config/pods/pod-pay/members/U-SOFIA/profile?role=developer")
    refused = client.get("/config/pods/pod-store/members/U-ZOE/profile", headers=_as("sm", "U-IRA"))

    assert before.status_code == 200, before.text
    body = before.json()
    assert body["current"] is None
    assert body["suggestion"] == {
        "profile": "qa",
        "label": "QA and test",
        "reasons": ["their title says 'QA Engineer'"],
    }
    assert refused.status_code == 403


def test_the_review_lists_unchecked_profiles_and_accept_all_checks_them(
    client: TestClient,
) -> None:
    admin = client.get("/config/profiles/review").json()["items"]
    ira = client.get("/config/profiles/review", headers=_as("sm", "U-IRA")).json()["items"]
    accepted = client.post(
        "/config/profiles/review/accept",
        json={
            "items": [
                {
                    "pod_id": item["current"]["pod_id"],
                    "member_id": item["current"]["member_id"],
                    "profile": item["suggestion"]["profile"],
                }
                for item in ira
            ]
        },
        headers=_as("sm", "U-IRA"),
    )
    not_hers = client.post(
        "/config/profiles/review/accept",
        json={"items": [{"pod_id": "pod-store", "member_id": "U-ZOE", "profile": "qa"}]},
        headers=_as("sm", "U-IRA"),
    )
    after = client.get("/config/profiles/review").json()["items"]

    assert {item["current"]["member_id"] for item in admin} == {"U-IRA", "U-KAI", "U-ZOE"}
    assert {item["current"]["pod_id"] for item in ira} == {"pod-pay"}
    assert accepted.status_code == 200, accepted.text
    assert {item["source"] for item in accepted.json()["accepted"]} == {"suggestion_accepted"}
    assert not_hers.status_code == 403
    assert [item["current"]["member_id"] for item in after] == ["U-ZOE"]


def test_a_developer_has_no_review_list(client: TestClient) -> None:
    assert client.get("/config/profiles/review", headers=_as("dev", "U-KAI")).status_code == 403


def test_the_import_puts_people_in_a_pod_with_their_role_and_profile(client: TestClient) -> None:
    imported = client.post(
        "/config/members/from-directory",
        json={
            "external_ids": ["U-NEW"],
            "pod_id": "pod-store",
            "role": "developer",
            "profile": "security",
        },
    )
    no_pod = client.post(
        "/config/members/from-directory", json={"external_ids": ["U-NEW"], "profile": "qa"}
    )
    bad_profile = client.post(
        "/config/members/from-directory",
        json={"external_ids": ["U-LATE"], "pod_id": "pod-store", "profile": "astronaut"},
    )
    store = client.get("/pods/pod-store/profiles").json()

    assert imported.status_code == 201, imported.text
    noor = next(item for item in store["members"] if item["member_id"] == "U-NEW")
    assert (noor["role"], noor["profile"], noor["source"]) == ("developer", "security", "import")
    assert no_pod.status_code == 422
    assert bad_profile.status_code == 422
    # Refused before anyone was imported.
    assert client.get("/config/members/U-LATE").status_code == 404


def test_a_member_reads_their_own_profiles(client: TestClient) -> None:
    client.put("/config/pods/pod-pay/members/U-KAI/profile", json={"profile": "ops"})
    mine = client.get("/me/profiles", headers=_as("dev", "U-KAI"))

    assert mine.status_code == 200
    assert [(item["pod_name"], item["label"]) for item in mine.json()] == [
        ("Payments Pod", "Ops / SRE / platform")
    ]


def test_a_hidden_profile_cannot_be_picked(client: TestClient) -> None:
    client.put("/config/profiles/design", json={"label": "UX and design", "hidden": True})
    refused = client.put("/config/pods/pod-pay/members/U-KAI/profile", json={"profile": "design"})

    assert refused.status_code == 422
    assert "is hidden" in refused.json()["detail"]


def test_a_profile_name_is_one_per_tenant_whatever_its_case(client: TestClient) -> None:
    taken = client.put("/config/profiles/quality", json={"label": "  qa AND   test "})
    hidden_too = client.put(
        "/config/profiles/design", json={"label": "UX and design", "hidden": True}
    )
    of_hidden = client.put("/config/profiles/look", json={"label": "ux and DESIGN"})
    own_name = client.put("/config/profiles/qa", json={"label": "QA AND TEST"})
    fresh = client.put("/config/profiles/compliance", json={"label": "Compliance"})

    assert taken.status_code == 409
    assert taken.json()["detail"] == (
        "Another profile (qa) is already called 'QA and test'. Give this one a different name."
    )
    assert hidden_too.status_code == 200
    # A hidden profile keeps its name: it can be offered again.
    assert of_hidden.status_code == 409
    # Renaming a profile to its own name in other letters is not a clash.
    assert own_name.status_code == 200
    assert own_name.json()["label"] == "QA AND TEST"
    assert fresh.status_code == 200


def test_another_tenants_profile_names_never_clash() -> None:
    async def run() -> None:
        service, _graph = await _service([date(2026, 10, 5)])
        await service.save_catalog_entry(
            "other",
            ProfileDefinition(slug="compliance", label="Compliance", code_work=False),
            by="U-ADMIN",
        )
        saved = await service.save_catalog_entry(
            TENANT,
            ProfileDefinition(slug="audit", label="COMPLIANCE", code_work=False),
            by="U-ADMIN",
        )
        assert saved.label == "COMPLIANCE"
        with pytest.raises(ProfileConflict, match="already called 'COMPLIANCE'"):
            await service.save_catalog_entry(
                TENANT,
                ProfileDefinition(slug="checks", label="compliance", code_work=False),
                by="U-ADMIN",
            )

    asyncio.run(run())


# ---- the day report ------------------------------------------------------------------------

_NAMES = {"u-mina": "Mina Patel", "u-sofia": "Sofia Bergmann", "u-kai": "Kai Thompson"}
_PAY = team_from(Pod(tenant_id=TENANT, id="pod-pay", name="Payments Pod"), _NAMES)
_STORE = team_from(Pod(tenant_id=TENANT, id="pod-store", name="Storefront Pod"), _NAMES)


def _report_scope() -> AskScope:
    return AskScope(
        project=Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        tasks={},
        teams={"pod-pay": _PAY, "pod-store": _STORE},
        all_teams={"pod-pay": _PAY, "pod-store": _STORE},
        members=frozenset(_NAMES),
        names=_NAMES,
        assignees={},
        task_teams={},
        member_teams={"u-mina": "pod-pay", "u-sofia": "pod-store", "u-kai": "pod-pay"},
        member_team_ids={
            "u-mina": ("pod-pay", "pod-store"),
            "u-sofia": ("pod-store",),
            "u-kai": ("pod-pay",),
        },
        profiles={
            "pod-pay": {"u-mina": "product", "u-kai": "development"},
            "pod-store": {"u-mina": "product", "u-sofia": "qa"},
        },
        checked_profiles={
            "pod-pay": {"u-mina": "product"},
            "pod-store": {"u-mina": "product", "u-sofia": "qa"},
        },
        profile_labels={
            "product": "Product",
            "qa": "QA and test",
            "development": "Development",
            "security": "Security",
        },
    )


def test_an_owner_is_named_with_the_profile_someone_chose_for_them() -> None:
    scope = _report_scope()

    assert owner_heading("Mina Patel", scope) == (
        "Mina Patel (Product, Payments Pod and Storefront Pod)"
    )
    assert owner_heading("Sofia Bergmann", scope) == "Sofia Bergmann (QA and test, Storefront Pod)"
    # Kai's profile only mirrors his pod role: nothing to add.
    assert owner_heading("Kai Thompson", scope) == "Kai Thompson"
    assert owner_heading(None, scope) == "Nobody named yet"


def test_a_profile_is_held_in_the_scopes_pods_first_by_name() -> None:
    scope = _report_scope()

    assert profile_holder(scope, "qa") == ("Sofia Bergmann", _STORE)
    assert profile_holder(scope, "qa", "pod-pay") == (None, _PAY)
    assert profile_holder(scope, "product") == ("Mina Patel", _PAY)


def _gap(owner_profile: str, scope: ScopeRef) -> ReadinessGap:
    return ReadinessGap(
        finding_id="f-1",
        scope=scope,
        line="Release 1 needs a test plan within 4 working days, and none is in Jira.",
        ask="Release 1: no test plan in Jira yet (due 9 Oct); create it or link one",
        waited_days=0,
        urgency=UrgencyKind.DUE_SOON,
        entered_on=date(2026, 10, 5),
        owner_profile=owner_profile,
        fix_ask="Release 1: create or link the test plan (due 9 Oct)" if owner_profile else "",
    )


def test_a_criterion_with_a_profile_adds_a_fix_ask_to_its_holder() -> None:
    scope = _report_scope()
    release = ScopeRef(kind=ScopeKind.RELEASE, id="r1")
    matrix = default_matrix(TENANT, "checkout")
    day = date(2026, 10, 5)

    plain = _readiness_asks(ReportGaps(gaps=(_gap("", release),)), scope, matrix, day)
    with_qa = _readiness_asks(ReportGaps(gaps=(_gap("qa", release),)), scope, matrix, day)
    nobody = _readiness_asks(ReportGaps(gaps=(_gap("security", release),)), scope, matrix, day)

    assert [ask.need for ask in plain] == [NeedType.DECISION]
    # The decision stays with the decider's own rule: a profile grants no rights,
    # so Mina's product profile does not make her the one who decides.
    assert [(ask.need, ask.owner) for ask in with_qa] == [
        (NeedType.DECISION, None),
        (NeedType.FIX, "Sofia Bergmann"),
    ]
    assert with_qa[1].text == "Release 1: create or link the test plan (due 9 Oct)"
    assert with_qa[1].team == _STORE
    assert nobody[1].owner is None
    assert nobody[1].text.endswith("; nobody in its pods has the Security profile yet")


# ---- Ora's team_profiles ---------------------------------------------------------------------


class _Asked:
    """The readiness read team_profiles uses: the profiles the criteria ask for."""

    async def asked_profiles(self, tenant_id: str) -> dict[str, tuple[str, ...]]:
        return {"security": ("Security review",), "development": ("Load test",)}


async def _team(today: date) -> tuple[TeamProfilesTool, ProfileService]:
    service, graph = await _service([today])
    await graph.upsert_node(Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"))
    await graph.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="checkout", to_node_id="pod-pay", kind=EdgeKind.CONTAINS
        )
    )
    tool = TeamProfilesTool(
        tenant_id=TENANT,
        service=service,
        repository=graph,
        readiness=_Asked(),  # type: ignore[arg-type]
        as_of=today,
    )
    return tool, service


def test_team_profiles_names_each_pods_people_and_the_profiles_nobody_holds() -> None:
    async def run() -> None:
        today = date(2026, 10, 5)
        tool, service = await _team(today)
        await service.set_profile(
            TENANT, "pod-pay", "U-KAI", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
        )

        project = json.loads(await tool.run({"project_id": "checkout"}))
        qa = json.loads(await tool.run({"pod_id": "pod-pay", "profile": "QA"}))
        security = json.loads(await tool.run({"pod_id": "pod-pay", "profile": "security"}))
        unknown = json.loads(await tool.run({"profile": "astronaut"}))

        assert project["pods"] == [
            {
                "pod_id": "pod-pay",
                "pod_name": "Payments Pod",
                "members": [
                    {
                        "name": "Kai",
                        "role_in_pod": "developer",
                        "profile": "qa",
                        "profile_label": "QA and test",
                        "checked": True,
                    }
                ],
            }
        ]
        # Nobody in Checkout's pods is security, and nobody development any more.
        assert [item["profile"] for item in project["missing"]] == ["development", "security"]
        assert project["missing"][1]["asked_by_criteria"] == ["Security review"]
        assert [member["name"] for member in qa["pods"][0]["members"]] == ["Kai"]
        assert security["pods"][0]["members"] == []
        assert unknown["error"].startswith("no profile 'astronaut'")

    asyncio.run(run())


class _Request:
    """What the dependency helpers read of a request: the app's state."""

    def __init__(self, app: object) -> None:
        self.app = app


def test_team_profiles_is_offered_to_whoever_may_ask_and_never_to_a_developer(
    client: TestClient,
) -> None:
    service = get_ask_service(_Request(client.app))  # type: ignore[arg-type]

    def tools(role: Role) -> set[str]:
        principal = Principal(tenant_id=TENANT, subject="U-X", roles=frozenset({role}))
        return {tool.name for tool in service.tools_for(principal, date.today())}

    assert "team_profiles" in tools(Role.SM)
    assert "team_profiles" in tools(Role.EXEC)
    assert tools(Role.DEV) == set()


def test_a_readiness_criterion_keeps_the_profile_it_asks(client: TestClient) -> None:
    saved = client.put(
        "/config/readiness/criteria", json={**CRITERION, "owner_profile": "security"}
    )
    bad = client.put("/config/readiness/criteria", json={**CRITERION, "owner_profile": "Not OK"})
    listed = client.get("/config/readiness").json()

    assert saved.status_code == 200, saved.text
    assert saved.json()["owner_profile"] == "security"
    assert bad.status_code == 422
    assert "profile to ask" in bad.json()["detail"]
    assert [item["owner_profile"] for item in listed["criteria"]] == ["security"]
