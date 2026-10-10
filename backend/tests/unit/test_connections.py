"""Connections an admin sets up: what is accepted, where secrets go, and who may change them."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg import OperationalError
from psycopg_pool import PoolClosed, PoolTimeout

from api.main import create_app
from config.settings import Settings
from core.application.connection_service import (
    ConnectionConflict,
    ConnectionDraft,
    ConnectionService,
    ConnectionTestRefused,
    StoredConnections,
    UnknownConnector,
)
from core.domain.connections import (
    ConnectionCheck,
    ConnectionTestOutcome,
    ConnectionValidationError,
    ConnectionValues,
    FieldKind,
    applicable_fields,
    missing_required,
    normalize_settings,
    rerouted_fields,
)
from core.domain.graph import Developer
from core.domain.integrations import Issue, IssueState, SyncCursor
from core.ports.secrets import SecretRef
from infra.adapters.connections import resolver as resolver_module
from infra.adapters.connections.resolver import CachedConnectionResolver
from infra.adapters.connections.routing import TenantRoutedIssueTracker, TenantRoutedVcsProvider
from infra.adapters.connections.specs import (
    ALL_SPECS,
    EMAIL_SPEC,
    GITHUB_SPEC,
    GITLAB_SPEC,
    JIRA_SPEC,
    SettingsConnectorCatalog,
)
from infra.adapters.integrations.fake import FakeIssueTracker, FakeVcsProvider
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_connections import InMemoryConnectionRepository
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_connections import PostgresConnectionRepository

TENANT = "demo"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
JIRA_DC = {
    "deployment": "data_center",
    "base_url": "https://jira.example.com/",
    "auth_method": "personal_access_token",
}


@dataclass
class _RecordingTester:
    result: ConnectionCheck = field(
        default_factory=lambda: ConnectionCheck(ok=True, message="Connected to Jira.")
    )
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    async def test(
        self, tenant_id: str, connector: str, values: Mapping[str, str]
    ) -> ConnectionCheck:
        del tenant_id
        self.calls.append((connector, dict(values)))
        return self.result


@dataclass
class _Harness:
    service: ConnectionService
    repository: InMemoryConnectionRepository
    secrets: FernetSecretStore
    records: InMemoryEncryptedSecretRecordStore
    tester: _RecordingTester
    changes: list[str]


def _harness(*, graph: InMemoryGraphStore | None = None, **settings: object) -> _Harness:
    repository = InMemoryConnectionRepository()
    records = InMemoryEncryptedSecretRecordStore()
    secrets = FernetSecretStore(Fernet(Fernet.generate_key()), records)
    tester = _RecordingTester()
    changes: list[str] = []
    service = ConnectionService(
        repository=repository,
        secret_store=secrets,
        catalog=SettingsConnectorCatalog(_settings(**settings)),
        tester=tester,
        clock=lambda: NOW,
        on_change=changes.append,
        graph_repository=graph,
    )
    return _Harness(service, repository, secrets, records, tester, changes)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "secret_key": "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        "runtime_mode": "memory",
        "dev_principal_roles": "admin",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# --- What the fields accept ----------------------------------------------------


def test_settings_are_trimmed_and_urls_lose_their_trailing_slash() -> None:
    normalized = normalize_settings(JIRA_SPEC, {**JIRA_DC, "story_points_field": "  "})

    assert normalized == {
        "deployment": "data_center",
        "base_url": "https://jira.example.com",
        "auth_method": "personal_access_token",
    }


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"base_url": "ftp://jira"}, r"Jira address must be an http\(s\) address"),
        ({"base_url": "https://user:pw@jira.example.com"}, "must not carry a user name"),
        ({"base_url": "https://jira.example.com/?token=x"}, "must not carry a query"),
        ({"deployment": "on_prem"}, "Jira type must be one of"),
        ({"email": "not-an-address"}, "Account email must be an email address"),
        ({"api_token": "abc"}, "API token is a secret and is set on its own"),
        ({"colour": "red"}, "Jira has no field 'colour'"),
    ],
)
def test_values_that_do_not_fit_their_field_are_refused(
    settings: dict[str, str], message: str
) -> None:
    with pytest.raises(ConnectionValidationError, match=message):
        normalize_settings(JIRA_SPEC, settings)


def test_every_problem_is_named_in_one_sentence() -> None:
    with pytest.raises(ConnectionValidationError) as caught:
        normalize_settings(EMAIL_SPEC, {"port": "zero", "from_address": "nobody"})

    message = str(caught.value)
    assert "Port must be a whole number above zero" in message
    assert "From address must be an email address" in message
    assert message.endswith(".")


def test_an_option_that_does_not_fit_the_other_choices_is_refused() -> None:
    # A personal access token is a Data Center sign-in; Jira Cloud has none.
    with pytest.raises(ConnectionValidationError, match="Personal access token is not available"):
        normalize_settings(
            JIRA_SPEC, {"deployment": "cloud", "auth_method": "personal_access_token"}
        )


def test_only_the_fields_of_the_chosen_sign_in_apply() -> None:
    keys = {item.key for item in applicable_fields(JIRA_SPEC, JIRA_DC)}

    assert "personal_access_token" in keys
    assert {"email", "api_token", "username", "password"}.isdisjoint(keys)


def test_missing_required_counts_stored_secrets_and_defaults() -> None:
    assert missing_required(JIRA_SPEC, JIRA_DC, frozenset()) == ("Personal access token",)
    assert missing_required(JIRA_SPEC, JIRA_DC, frozenset({"personal_access_token"})) == ()
    # The Jira type and sign-in method have defaults, so only the rest is missing.
    assert missing_required(JIRA_SPEC, {}, frozenset()) == (
        "Jira address",
        "Account email",
        "API token",
    )


def test_every_connector_spec_is_consistent() -> None:
    for spec in ALL_SPECS:
        keys = [item.key for item in spec.fields]
        assert len(keys) == len(set(keys)), spec.id
        for item in spec.fields:
            for condition in (item.shown_when, *(option.shown_when for option in item.options)):
                if condition is not None:
                    assert condition.field in keys, (spec.id, item.key)
            if item.default is not None and item.options:
                assert item.default in {option.value for option in item.options}


# --- Saving, secrets and enabling ---------------------------------------------------


async def test_a_secret_is_encrypted_and_never_kept_with_the_settings() -> None:
    harness = _harness()

    view = await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "pat-123"},
        actor="admin@example.com",
    )

    connection = view.connection
    assert connection is not None
    assert connection.secret_keys == frozenset({"personal_access_token"})
    assert "pat-123" not in repr(connection)
    ref = SecretRef(tenant_id=TENANT, connector="jira", key="personal_access_token")
    assert await harness.secrets.get(ref) == "pat-123"
    assert b"pat-123" not in harness.records._records[ref]
    assert harness.changes == [TENANT]


async def test_a_secret_left_out_is_kept_and_a_blank_one_is_cleared() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "email",
        enabled=False,
        settings={"host": "smtp.example.com", "username": "bot"},
        secrets={"password": "first"},
        actor="admin",
    )

    kept = await harness.service.save(
        TENANT,
        "email",
        enabled=False,
        settings={"host": "smtp.example.com", "username": "bot"},
        secrets={},
        actor="admin",
    )
    assert kept.connection is not None and kept.connection.secret_keys == frozenset({"password"})

    cleared = await harness.service.save(
        TENANT,
        "email",
        enabled=False,
        settings={"host": "smtp.example.com"},
        secrets={"password": None},
        actor="admin",
    )
    assert cleared.connection is not None and cleared.connection.secret_keys == frozenset()
    assert harness.records._records == {}


async def test_a_connection_can_be_saved_incomplete_while_it_is_off() -> None:
    harness = _harness()

    view = await harness.service.save(
        TENANT, "jira", enabled=False, settings={"deployment": "data_center"}, secrets={}, actor="a"
    )

    assert view.connection is not None and view.connection.enabled is False


async def test_a_connection_names_the_member_who_saved_it() -> None:
    graph = InMemoryGraphStore()
    await graph.upsert_node(Developer(tenant_id=TENANT, id="U-ASHA", name="Asha Rao"))
    harness = _harness(graph=graph)

    saved = await harness.service.save(
        TENANT, "jira", enabled=False, settings=JIRA_DC, secrets={}, actor="U-ASHA"
    )
    await harness.service.save(
        TENANT, "gitlab", enabled=False, settings={}, secrets={}, actor="ci-bot"
    )
    read = await harness.service.connection(TENANT, "jira")
    listed = {view.spec.id: view for view in await harness.service.list_connections(TENANT)}

    assert saved.updated_by_name == read.updated_by_name == "Asha Rao"
    assert listed["jira"].updated_by_name == "Asha Rao"
    # An id that is no member's keeps no name: the console shows the id itself.
    gitlab = listed["gitlab"]
    assert gitlab.updated_by_name is None
    assert gitlab.connection is not None and gitlab.connection.updated_by == "ci-bot"
    assert listed["github"].updated_by_name is None


async def test_turning_a_connection_on_needs_every_required_field_that_applies() -> None:
    harness = _harness()

    with pytest.raises(ConnectionValidationError, match="Fill in Personal access token"):
        await harness.service.save(
            TENANT, "jira", enabled=True, settings=JIRA_DC, secrets={}, actor="a"
        )
    assert await harness.repository.get(TENANT, "jira") is None


async def test_only_one_code_host_can_be_on() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "gitlab",
        enabled=True,
        settings={"base_url": "https://gitlab.example.com"},
        secrets={"token": "glpat"},
        actor="a",
    )

    with pytest.raises(ConnectionConflict, match="GitLab is already on"):
        await harness.service.save(
            TENANT, "github", enabled=True, settings={}, secrets={"token": "ghp"}, actor="a"
        )
    # Off is always allowed, so the admin can prepare the other one.
    await harness.service.save(
        TENANT, "github", enabled=False, settings={}, secrets={"token": "ghp"}, actor="a"
    )


async def test_an_unknown_connector_or_secret_is_refused() -> None:
    harness = _harness()

    with pytest.raises(UnknownConnector):
        await harness.service.save(
            TENANT, "jenkins", enabled=False, settings={}, secrets={}, actor="a"
        )
    with pytest.raises(ConnectionValidationError, match="Jira has no secret 'base_url'"):
        await harness.service.save(
            TENANT, "jira", enabled=False, settings={}, secrets={"base_url": "x"}, actor="a"
        )


async def test_a_change_forgets_the_last_test_and_a_plain_resave_keeps_it() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )
    await harness.service.test(TENANT, "jira")
    tested = await harness.repository.get(TENANT, "jira")
    assert tested is not None and tested.last_test is not None

    same = await harness.service.save(
        TENANT, "jira", enabled=True, settings=JIRA_DC, secrets={}, actor="a"
    )
    assert same.connection is not None and same.connection.last_test is not None

    changed = await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings={**JIRA_DC, "base_url": "https://jira2.example.com"},
        secrets={},
        actor="a",
    )
    assert changed.connection is not None and changed.connection.last_test is None


async def test_remove_forgets_the_connection_and_every_secret() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )

    await harness.service.remove(TENANT, "jira")
    await harness.service.remove(TENANT, "jira")

    assert await harness.repository.get(TENANT, "jira") is None
    assert harness.records._records == {}


# --- Testing --------------------------------------------------------------------


async def test_testing_the_stored_values_records_the_outcome() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )

    view = await harness.service.test(TENANT, "jira")

    assert view.recorded is True
    assert harness.tester.calls == [
        (
            "jira",
            {
                "deployment": "data_center",
                "base_url": "https://jira.example.com",
                "auth_method": "personal_access_token",
                "personal_access_token": "p",
            },
        )
    ]
    stored = await harness.repository.get(TENANT, "jira")
    assert stored is not None
    assert stored.last_test == ConnectionTestOutcome(
        ok=True, message="Connected to Jira.", tested_at=NOW
    )


async def test_a_draft_is_tested_over_the_stored_values_and_not_recorded() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )

    view = await harness.service.test(
        TENANT,
        "jira",
        # The address and sign-in are as saved, so the stored token may go there.
        draft=ConnectionDraft(settings={"story_points_field": "customfield_10016"}, secrets={}),
    )

    assert view.recorded is False
    assert harness.tester.calls[-1][1] == {
        "deployment": "data_center",
        "base_url": "https://jira.example.com",
        "auth_method": "personal_access_token",
        "personal_access_token": "p",
        "story_points_field": "customfield_10016",
    }
    stored = await harness.repository.get(TENANT, "jira")
    assert stored is not None and stored.last_test is None


async def test_a_test_with_a_required_field_missing_never_reaches_the_system() -> None:
    harness = _harness()

    view = await harness.service.test(
        TENANT, "jira", draft=ConnectionDraft(settings=JIRA_DC, secrets={})
    )

    assert view.check.ok is False
    assert view.check.message == "Fill in Personal access token first."
    assert harness.tester.calls == []


# --- A draft test and the stored secrets ------------------------------------------------

# One saved connection per way a secret is sent: (connector, settings, secrets).
_SAVED: dict[str, tuple[str, dict[str, str | None], dict[str, str | None]]] = {
    "jira-pat": ("jira", dict(JIRA_DC), {"personal_access_token": "stored-pat"}),
    "jira-cloud": (
        "jira",
        {
            "deployment": "cloud",
            "base_url": "https://jira.example.com",
            "auth_method": "api_token",
            "email": "reader@example.com",
        },
        {"api_token": "stored-token"},
    ),
    "jira-basic": (
        "jira",
        {
            "deployment": "data_center",
            "base_url": "https://jira.example.com",
            "auth_method": "basic",
            "username": "reader",
        },
        {"password": "stored-password"},
    ),
    "gitlab": (
        "gitlab",
        {"base_url": "https://gitlab.example.com", "namespace_id": "acme"},
        {"token": "stored-token"},
    ),
    "github": (
        "github",
        {"base_url": "https://github.example.com/api/v3", "owner": "acme"},
        {"token": "stored-token"},
    ),
    "email": (
        "email",
        {
            "host": "smtp.example.com",
            "port": "587",
            "security": "starttls",
            "username": "reports",
            "from_address": "reports@example.com",
        },
        {"password": "stored-password"},
    ),
    "calendar": ("google_calendar", {"calendar_id": "leave"}, {"token": "stored-token"}),
    "teams": (
        "teams",
        {"channel_name": "Delivery"},
        {"webhook_url": "https://teams.example.com/hooks/1"},
    ),
}
_REFUSAL = (
    " changed. A stored secret is used only with the address and sign-in it was saved with, "
    "so enter {secret} again to test the new values."
)


async def _saved(name: str) -> tuple[_Harness, str]:
    connector, settings, secrets = _SAVED[name]
    harness = _harness()
    await harness.service.save(
        TENANT, connector, enabled=False, settings=settings, secrets=secrets, actor="a"
    )
    return harness, connector


@pytest.mark.parametrize(
    ("name", "draft", "changed", "secret"),
    [
        (
            "jira-pat",
            {"base_url": "https://jira.example.org"},
            "Jira address",
            "Personal access token",
        ),
        ("jira-cloud", {"email": "someone@example.com"}, "Account email", "API token"),
        ("jira-basic", {"username": "admin"}, "User name", "Password"),
        ("gitlab", {"base_url": "https://gitlab.example.org"}, "GitLab address", "Access token"),
        # Cleared, the address falls back to its default: another host.
        ("gitlab", {"base_url": None}, "GitLab address", "Access token"),
        ("github", {"base_url": "https://api.github.com"}, "API address", "Access token"),
        ("email", {"host": "smtp.example.org", "port": "25"}, "SMTP server and Port", "Password"),
        # Turning encryption off would send the stored password in clear.
        ("email", {"security": "none"}, "Encryption", "Password"),
        ("email", {"username": "someone-else"}, "User name", "Password"),
        (
            "calendar",
            {"base_url": "https://calendar.example.com/v3"},
            "API address",
            "Access token",
        ),
    ],
)
async def test_a_draft_that_moves_an_address_or_user_never_takes_the_stored_secret_there(
    name: str, draft: dict[str, str | None], changed: str, secret: str
) -> None:
    harness, connector = await _saved(name)

    with pytest.raises(ConnectionTestRefused) as refused:
        await harness.service.test(
            TENANT, connector, draft=ConnectionDraft(settings=draft, secrets={})
        )

    assert str(refused.value) == changed + _REFUSAL.format(secret=secret)
    assert harness.tester.calls == []


async def test_a_draft_that_moves_the_address_is_tested_there_with_the_secret_typed_again() -> None:
    harness, _ = await _saved("jira-pat")

    view = await harness.service.test(
        TENANT,
        "jira",
        draft=ConnectionDraft(
            settings={"base_url": "https://jira.example.org"},
            secrets={"personal_access_token": "typed-again"},
        ),
    )

    assert view.recorded is False
    [(_, values)] = harness.tester.calls
    assert values["base_url"] == "https://jira.example.org"
    assert values["personal_access_token"] == "typed-again"


@pytest.mark.parametrize(
    ("name", "draft", "secret_key", "secret"),
    [
        # Read on the same host with the same sign-in: nothing to type again.
        (
            "jira-pat",
            {"story_points_field": "customfield_10016"},
            "personal_access_token",
            "stored-pat",
        ),
        (
            "jira-pat",
            {"base_url": "https://jira.example.com/"},
            "personal_access_token",
            "stored-pat",
        ),
        ("gitlab", {"namespace_id": "platform"}, "token", "stored-token"),
        (
            "teams",
            {"channel_name": "Delivery daily"},
            "webhook_url",
            "https://teams.example.com/hooks/1",
        ),
        ("email", {"from_address": "noreply@example.com"}, "password", "stored-password"),
    ],
)
async def test_a_draft_that_keeps_the_address_and_sign_in_reuses_the_stored_secret(
    name: str, draft: dict[str, str | None], secret_key: str, secret: str
) -> None:
    harness, connector = await _saved(name)

    view = await harness.service.test(
        TENANT, connector, draft=ConnectionDraft(settings=draft, secrets={})
    )

    assert view.check.ok is True
    [(_, values)] = harness.tester.calls
    assert values[secret_key] == secret


@pytest.mark.parametrize("cleared", [None, "", "  "])
async def test_a_secret_cleared_in_a_draft_is_never_taken_from_the_store(
    cleared: str | None,
) -> None:
    harness, _ = await _saved("jira-pat")

    view = await harness.service.test(
        TENANT,
        "jira",
        draft=ConnectionDraft(settings={}, secrets={"personal_access_token": cleared}),
    )

    assert view.check.ok is False
    assert view.check.message == "Fill in Personal access token first."
    assert harness.tester.calls == []


async def test_a_draft_clears_an_optional_secret_or_setting_without_reusing_the_stored_one() -> (
    None
):
    mail, _ = await _saved("email")
    code, _ = await _saved("gitlab")

    # Nothing stored is reused, so a moved server is fine: it gets no password.
    await mail.service.test(
        TENANT,
        "email",
        draft=ConnectionDraft(settings={"host": "smtp.example.org"}, secrets={"password": None}),
    )
    await code.service.test(
        TENANT, "gitlab", draft=ConnectionDraft(settings={"namespace_id": None}, secrets={})
    )

    [(_, mail_values)] = mail.tester.calls
    assert mail_values["host"] == "smtp.example.org"
    assert "password" not in mail_values
    [(_, code_values)] = code.tester.calls
    assert "namespace_id" not in code_values
    assert code_values["token"] == "stored-token"


def test_a_field_counts_as_moved_only_while_it_applies() -> None:
    saved = normalize_settings(JIRA_SPEC, JIRA_DC)

    # The account email belongs to another sign-in method: it sends nothing.
    assert rerouted_fields(JIRA_SPEC, saved, {**saved, "email": "x@example.com"}) == ()
    assert [
        item.key
        for item in rerouted_fields(JIRA_SPEC, saved, {**saved, "base_url": "https://x.example"})
    ] == ["base_url"]
    # A default compares as its value: the saved default is not a change.
    assert rerouted_fields(GITLAB_SPEC, {}, {"base_url": "https://gitlab.com"}) == ()


def test_the_fields_that_route_a_secret_are_its_address_encryption_and_sign_in() -> None:
    routing = {
        spec.id: sorted(item.key for item in spec.fields if item.routes_secrets)
        for spec in ALL_SPECS
    }

    assert routing == {
        "jira": ["auth_method", "base_url", "email", "username"],
        "gitlab": ["base_url"],
        "github": ["base_url"],
        "slack": [],
        "email": ["host", "port", "security", "username"],
        "teams": [],
        "google_calendar": ["base_url"],
    }
    # A secret is sent to every address field, so a new one must route secrets too.
    for spec in ALL_SPECS:
        for item in spec.fields:
            if item.kind is FieldKind.URL:
                assert item.routes_secrets, (spec.id, item.key)


# --- Reading a connection for adapters ----------------------------------------------


async def test_an_adapter_reads_only_the_fields_of_the_chosen_sign_in() -> None:
    harness = _harness()
    # A password stored for basic auth stays behind when the method changes.
    await harness.service.save(
        TENANT,
        "jira",
        enabled=False,
        settings={**JIRA_DC, "auth_method": "basic", "username": "svc"},
        secrets={"password": "old-password"},
        actor="a",
    )
    await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )

    values = await harness.service.resolve(TENANT, "jira")

    assert values == ConnectionValues(
        connector="jira",
        values={
            "deployment": "data_center",
            "base_url": "https://jira.example.com",
            "auth_method": "personal_access_token",
            "personal_access_token": "p",
        },
    )


async def test_a_connection_that_is_off_is_not_read() -> None:
    harness = _harness()
    await harness.service.save(
        TENANT,
        "jira",
        enabled=False,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )

    assert await harness.service.resolve(TENANT, "jira") is None
    assert await harness.service.resolve("other-tenant", "jira") is None


async def test_the_cache_serves_repeat_reads_until_invalidated() -> None:
    reads: list[str] = []

    class _Counting:
        async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
            reads.append(connector)
            return ConnectionValues(connector=connector, values={"token": str(len(reads))})

    now = [0.0]
    resolver = CachedConnectionResolver(_Counting(), ttl_seconds=30, clock=lambda: now[0])

    first = await resolver.resolve(TENANT, "gitlab")
    again = await resolver.resolve(TENANT, "gitlab")
    now[0] = 31
    expired = await resolver.resolve(TENANT, "gitlab")
    resolver.invalidate(TENANT)
    fresh = await resolver.resolve(TENANT, "gitlab")

    assert first == again
    assert [first.get("token"), expired.get("token"), fresh.get("token")] == ["1", "2", "3"]  # type: ignore[union-attr]


async def test_an_unreadable_connection_falls_back_to_the_server_settings() -> None:
    class _Broken:
        async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
            raise OSError("connection refused for postgresql://user:secret@db/openprogram")

    resolver = CachedConnectionResolver(_Broken())

    assert await resolver.resolve(TENANT, "jira") is None


_LEAKY_DSN = "postgresql://user:secret@db/openprogram"


@dataclass
class _RecordingLogger:
    warnings: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def warning(self, event: str, **fields: object) -> None:
        self.warnings.append((event, fields))


@pytest.fixture
def resolver_log(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    log = _RecordingLogger()
    monkeypatch.setattr(resolver_module, "_logger", log)
    return log


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeout("pool initialization incomplete after 30.0 sec"),
        PoolClosed("pool has already been opened/closed and cannot be reused"),
        OperationalError(f"connection to {_LEAKY_DSN} failed: Connection refused"),
        TimeoutError(),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_each_way_an_unreachable_store_fails_falls_back_and_logs_the_type_only(
    error: Exception, resolver_log: _RecordingLogger
) -> None:
    class _Unreachable:
        async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
            raise error

    assert await CachedConnectionResolver(_Unreachable()).resolve(TENANT, "slack") is None

    assert [fields["error_type"] for _, fields in resolver_log.warnings] == [type(error).__name__]
    assert "secret" not in repr(resolver_log.warnings)


async def test_a_connection_read_that_does_not_finish_falls_back_without_waiting(
    resolver_log: _RecordingLogger,
) -> None:
    # A database that went away under an open pool makes a read wait for a
    # connection for the pool's full 30 s: the resolver stops waiting first.
    class _Hanging:
        async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
            await asyncio.Event().wait()
            return None

    resolver = CachedConnectionResolver(_Hanging(), read_timeout_seconds=0.01)

    assert await resolver.resolve(TENANT, "slack") is None

    assert [fields["error_type"] for _, fields in resolver_log.warnings] == ["TimeoutError"]


# --- Routing calls to what the tenant turned on ------------------------------------------


class _OnlyFor:
    def __init__(self, *connectors: str) -> None:
        self._connectors = connectors

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        if tenant_id == TENANT and connector in self._connectors:
            return ConnectionValues(connector=connector, values={})
        return None


class _MarkedTracker(FakeIssueTracker):
    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        return Issue(tenant_id=tenant_id, key=key, title="from jira", state=IssueState.TODO)


async def test_the_tracker_follows_the_tenants_jira_connection() -> None:
    fallback = FakeIssueTracker(tenant_id=TENANT)
    jira = _MarkedTracker(tenant_id=TENANT)

    on = TenantRoutedIssueTracker(
        fallback=fallback, routes={"jira": jira}, connections=_OnlyFor("jira")
    )
    off = TenantRoutedIssueTracker(fallback=fallback, routes={"jira": jira}, connections=_OnlyFor())

    assert (await on.get_issue(TENANT, "CHK-1")).title == "from jira"
    assert (await off.list_issues_for_query(TENANT, "project = X", SyncCursor())) == (
        await fallback.list_issues_for_query(TENANT, "project = X", SyncCursor())
    )


async def test_the_code_host_follows_whichever_one_the_tenant_turned_on() -> None:
    fallback = FakeVcsProvider(tenant_id=TENANT)
    gitlab = FakeVcsProvider(tenant_id=TENANT)
    github = FakeVcsProvider(tenant_id=TENANT)
    routed = TenantRoutedVcsProvider(
        fallback=fallback,
        routes={"gitlab": gitlab, "github": github},
        connections=_OnlyFor("github"),
    )

    assert await routed._pick(TENANT) is github
    assert await routed._pick("other-tenant") is fallback


# --- Catalog ---------------------------------------------------------------------------


def test_the_catalog_says_which_connectors_the_environment_configures() -> None:
    catalog = SettingsConnectorCatalog(
        _settings(
            issue_tracker_provider="jira",
            jira_base_url="https://jira.example.com",
            jira_api_token="t",
            vcs_provider="gitlab",
            gitlab_token="g",
        )
    )

    assert catalog.environment_configured("jira") is True
    assert catalog.environment_configured("gitlab") is True
    assert catalog.environment_configured("github") is False
    assert catalog.environment_configured("email") is False
    assert {spec.id for spec in catalog.specs()} >= {"jira", "gitlab", "github", "slack", "email"}
    assert GITLAB_SPEC.exclusive_group == GITHUB_SPEC.exclusive_group == "code"


# --- API -------------------------------------------------------------------------------


def test_api_saves_reads_tests_and_removes_a_connection(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        listed = client.get("/config/integrations")
        saved = client.put(
            "/config/integrations/jira",
            json={
                "enabled": True,
                "settings": JIRA_DC,
                "secrets": {"personal_access_token": "pat-never-returned"},
            },
        )
        read = client.get("/config/integrations/jira")
        moved = client.post(
            "/config/integrations/jira/test",
            json={"settings": {"base_url": "https://jira.invalid"}, "secrets": {}},
        )
        cleared = client.post(
            "/config/integrations/jira/test",
            json={"secrets": {"personal_access_token": None}},
        )
        tested = client.post(
            "/config/integrations/jira/test",
            json={
                "settings": {"base_url": "https://jira.invalid"},
                "secrets": {"personal_access_token": "pat-typed-again"},
            },
        )
        client.post("/config/members", json={"id": "dev-user", "name": "Dana Admin"})
        named = client.get("/config/integrations/jira")
        removed = client.delete("/config/integrations/jira")
        after = client.get("/config/integrations/jira")

    assert listed.status_code == 200
    assert [item["connector"] for item in listed.json()][:3] == ["jira", "gitlab", "github"]
    assert saved.status_code == 200
    assert (saved.json()["updated_by"], saved.json()["updated_by_name"]) == ("dev-user", None)
    assert named.json()["updated_by_name"] == "Dana Admin"
    body = read.json()
    assert body["enabled"] is True
    assert body["settings"]["base_url"] == "https://jira.example.com"
    assert body["secrets_set"] == ["personal_access_token"]
    assert "pat-never-returned" not in saved.text + read.text
    # The stored token is never sent to an address nobody saved.
    assert moved.status_code == 400
    assert moved.json()["detail"] == (
        "Jira address changed. A stored secret is used only with the address and sign-in it "
        "was saved with, so enter Personal access token again to test the new values."
    )
    assert cleared.status_code == 200
    assert cleared.json()["message"] == "Fill in Personal access token first."
    assert tested.status_code == 200
    assert tested.json()["ok"] is False
    assert tested.json()["recorded"] is False
    assert removed.status_code == 204
    assert after.json()["configured"] is False


def test_api_answers_bad_input_with_a_sentence(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        unknown = client.get("/config/integrations/jenkins")
        invalid = client.put(
            "/config/integrations/jira",
            json={"enabled": False, "settings": {"base_url": "ftp://x"}},
        )
        incomplete = client.put(
            "/config/integrations/jira", json={"enabled": True, "settings": JIRA_DC}
        )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Jira address must be an http(s) address."
    assert incomplete.status_code == 422
    assert "Personal access token" in incomplete.json()["detail"]


@pytest.mark.parametrize("role", ["dev", "po", "sm", "mgr", "exec"])
def test_only_an_admin_reads_or_changes_connections(settings: Settings, role: str) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [
            client.get("/config/integrations"),
            client.get("/config/integrations/jira"),
            client.put("/config/integrations/jira", json={"enabled": False}),
            client.post("/config/integrations/jira/test"),
            client.delete("/config/integrations/jira"),
        ]

    assert [response.status_code for response in responses] == [403] * 5


# --- Postgres --------------------------------------------------------------------------


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


async def test_postgres_saves_settings_as_json_and_secret_names_only() -> None:
    executor = _RecordingExecutor()
    repository = PostgresConnectionRepository(executor)
    harness = _harness()
    view = await harness.service.save(
        TENANT,
        "jira",
        enabled=True,
        settings=JIRA_DC,
        secrets={"personal_access_token": "p"},
        actor="a",
    )
    assert view.connection is not None

    await repository.save(view.connection)

    query, params = executor.calls[0]
    assert "ON CONFLICT (tenant_id, connector) DO UPDATE" in query
    assert params[0:3] == (TENANT, "jira", True)
    assert params[3] == (
        '{"auth_method": "personal_access_token", "base_url": "https://jira.example.com", '
        '"deployment": "data_center"}'
    )
    assert params[4] == ["personal_access_token"]
    assert "p" not in [value for value in params if isinstance(value, str) and value == "p"]


async def test_postgres_reads_a_row_back_with_its_last_test() -> None:
    executor = _RecordingExecutor(
        rows=[
            {
                "tenant_id": TENANT,
                "connector": "gitlab",
                "enabled": True,
                "settings": {"base_url": "https://gitlab.example.com"},
                "secret_keys": ["token"],
                "updated_at": NOW,
                "updated_by": "admin",
                "last_test_ok": False,
                "last_test_message": "GitLab refused the access token.",
                "last_test_at": NOW,
            }
        ]
    )

    connection = await PostgresConnectionRepository(executor).get(TENANT, "gitlab")

    assert connection is not None
    assert connection.settings == {"base_url": "https://gitlab.example.com"}
    assert connection.secret_keys == frozenset({"token"})
    assert connection.last_test == ConnectionTestOutcome(
        ok=False, message="GitLab refused the access token.", tested_at=NOW
    )
    assert executor.calls[0][1] == (TENANT, "gitlab")


async def test_stored_connections_ignore_a_connector_the_catalog_does_not_know() -> None:
    harness = _harness()
    stored = StoredConnections(
        repository=harness.repository,
        secret_store=harness.secrets,
        catalog=SettingsConnectorCatalog(_settings()),
    )

    assert await stored.resolve(TENANT, "jenkins") is None
