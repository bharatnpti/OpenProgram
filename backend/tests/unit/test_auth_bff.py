from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.auth import Role
from core.ports.auth import AuthenticatedUser
from infra.adapters.auth.oidc import OidcBffService
from infra.adapters.auth.roles import map_oidc_roles
from infra.adapters.auth.session import InMemoryAuthSessionStore

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def test_dev_auth_status_returns_configured_principal() -> None:
    settings = _settings(auth_provider="dev", dev_principal_roles="admin,dev")
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "authenticated": True,
        "provider": "dev",
        "login_url": None,
        "message": None,
        "user": {
            "subject": "dev-user",
            "username": None,
            "email": None,
            "name": None,
            "roles": ["admin", "dev"],
            "scopes": ["dev-mode"],
        },
    }


def test_oidc_status_without_session_returns_login_url() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/status", headers={"referer": "http://localhost:5173/me"}
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["authenticated"] is False
    assert body["provider"] == "oidc_bff"
    assert body["message"] == "authentication required"
    assert body["login_url"].startswith("http://127.0.0.1:8000/api/v1/auth/login?")


def test_oidc_protected_route_without_session_returns_401_login_url() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/me/status", headers={"referer": "http://localhost:5173/me"})

    assert response.status_code == 401
    assert response.json()["detail"]["login_url"].startswith(
        "http://127.0.0.1:8000/api/v1/auth/login?"
    )


def test_oidc_valid_session_without_required_role_returns_403() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)
    session_id, csrf_token = _save_session(app, roles=frozenset({Role.DEV}))

    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, session_id)
        client.cookies.set(settings.auth_csrf_cookie_name, csrf_token)
        response = client.get("/config/programs")

    assert response.status_code == 403


def test_oidc_unsafe_request_requires_csrf_and_accepts_matching_token() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)
    session_id, csrf_token = _save_session(app, roles=frozenset({Role.ADMIN}))

    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, session_id)
        client.cookies.set(settings.auth_csrf_cookie_name, csrf_token)
        missing = client.post("/config/programs", json={"id": "p1", "name": "Program One"})
        accepted = client.post(
            "/config/programs",
            headers={settings.auth_csrf_header_name: csrf_token},
            json={"id": "p1", "name": "Program One"},
        )

    assert missing.status_code == 403
    assert accepted.status_code == 201
    assert accepted.json()["id"] == "p1"


def test_oidc_logout_deletes_local_session_and_requires_csrf() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)
    app.state.registry.oidc_bff_service()._metadata = {}
    session_id, csrf_token = _save_session(app, roles=frozenset({Role.ADMIN}))

    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, session_id)
        client.cookies.set(settings.auth_csrf_cookie_name, csrf_token)
        missing = client.post("/api/v1/auth/logout")
        response = client.post(
            "/api/v1/auth/logout",
            headers={settings.auth_csrf_header_name: csrf_token},
        )

    assert missing.status_code == 403
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "signed out",
        "redirect_url": "http://localhost:5173/logged-out",
    }
    assert asyncio.run(app.state.registry.auth_session_store().get_session(session_id)) is None


def test_oidc_login_redirect_stores_state_nonce_and_pkce() -> None:
    settings = _oidc_settings()
    store = InMemoryAuthSessionStore()
    service = OidcBffService(settings=settings, store=store)
    service._metadata = {"authorization_endpoint": "https://issuer.example.com/auth"}

    redirect_url = asyncio.run(
        service.authorization_redirect_url(return_url="/portfolio", prompt="login")
    )
    parsed = urlsplit(redirect_url)
    query = parse_qs(parsed.query)
    flow = asyncio.run(store.pop_flow(query["state"][0]))

    assert parsed.scheme == "https"
    assert parsed.netloc == "issuer.example.com"
    assert parsed.path == "/auth"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["openprogram"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8000/api/v1/auth/callback"]
    assert query["scope"] == ["openid profile email"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["login"]
    assert flow is not None
    assert flow.nonce == query["nonce"][0]
    assert flow.return_url == "http://localhost:5173/portfolio"
    assert flow.code_verifier


def test_oidc_role_mapping_filters_defaults_and_applies_configured_map() -> None:
    roles = map_oidc_roles(
        {
            "realm_access": {"roles": ["Admin", "uma_authorization", "default-roles-acme"]},
            "resource_access": {"openprogram": {"roles": ["Manager", "unknown"]}},
            "groups": ["Engineering"],
            "roles": ["offline_access", "Scrum-Master"],
        },
        client_id="openprogram",
        claim_paths=(
            "realm_access.roles",
            "resource_access.<client_id>.roles",
            "groups",
            "roles",
        ),
        role_map={
            "admin": Role.ADMIN,
            "manager": Role.MGR,
            "engineering": Role.DEV,
            "scrum-master": Role.SM,
        },
    )

    assert roles == frozenset({Role.ADMIN, Role.MGR, Role.DEV, Role.SM})


def test_expired_oidc_session_returns_401() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)
    session_id, csrf_token = _save_session(
        app,
        roles=frozenset({Role.ADMIN}),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, session_id)
        client.cookies.set(settings.auth_csrf_cookie_name, csrf_token)
        response = client.get("/config/programs")

    assert response.status_code == 401


def test_expired_oidc_token_metadata_returns_401_and_deletes_session() -> None:
    settings = _oidc_settings()
    app = create_app(settings=settings)
    session_id, csrf_token = _save_session(
        app,
        roles=frozenset({Role.ADMIN}),
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with TestClient(app) as client:
        client.cookies.set(settings.auth_cookie_name, session_id)
        client.cookies.set(settings.auth_csrf_cookie_name, csrf_token)
        response = client.get("/config/programs")

    assert response.status_code == 401
    assert asyncio.run(app.state.registry.auth_session_store().get_session(session_id)) is None


def _save_session(
    app: object,
    *,
    roles: frozenset[Role],
    expires_at: datetime | None = None,
    token_expires_at: datetime | None = None,
) -> tuple[str, str]:
    session = asyncio.run(
        app.state.registry.auth_session_store().save_session(
            session_id="session-" + datetime.now(UTC).strftime("%H%M%S%f"),
            user=AuthenticatedUser(
                tenant_id="demo",
                subject="oidc-user",
                roles=roles,
                scopes=frozenset({"openid"}),
                username="oidc-user",
                email="oidc@example.com",
                name="OIDC User",
                token_expires_at=token_expires_at,
            ),
            csrf_token="csrf-token",
            expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=10),
            token_material={"access_token": "secret-token"},
            ttl_seconds=600,
        )
    )
    return session.session_id, session.csrf_token


def _oidc_settings() -> Settings:
    return _settings(
        auth_provider="oidc_bff",
        oidc_issuer_url="https://issuer.example.com",
        oidc_client_id="openprogram",
        oidc_client_secret="secret",
    )


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, secret_key=SECRET_KEY, runtime_mode="memory", **overrides)
