from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.jose import JoseError, JsonWebKey, JsonWebToken

from config.settings import Settings
from core.domain.auth import Principal
from core.domain.errors import AuthenticationRequired, ProviderConfigurationError
from core.ports.auth import (
    AuthCallbackResult,
    AuthCredentials,
    AuthenticatedUser,
    AuthLogoutResult,
)
from infra.adapters.auth.roles import map_oidc_roles
from infra.adapters.auth.session import AuthFlowState, AuthSessionStore

_SIGNING_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]


@dataclass
class OidcBffAuthProvider:
    store: AuthSessionStore

    async def authenticate(self, credentials: AuthCredentials) -> Principal:
        if credentials.session_id is None:
            raise AuthenticationRequired("authentication session is missing")
        record = await self.store.get_session(credentials.session_id)
        if record is None:
            raise AuthenticationRequired("authentication session is invalid or expired")
        token_expires_at = record.session.user.token_expires_at
        if token_expires_at is not None and token_expires_at <= datetime.now(UTC):
            await self.store.delete_session(credentials.session_id)
            raise AuthenticationRequired("authentication token is expired")
        return record.session.user.principal()


@dataclass
class OidcBffService:
    settings: Settings
    store: AuthSessionStore
    _metadata: dict[str, object] | None = field(default=None, init=False)
    _jwks: dict[str, object] | None = field(default=None, init=False)

    async def authorization_redirect_url(
        self,
        *,
        return_url: str | None,
        prompt: str | None,
    ) -> str:
        metadata = await self._provider_metadata()
        authorization_endpoint = _required_url(metadata, "authorization_endpoint")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        flow = AuthFlowState(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            return_url=self.settings.safe_auth_return_url(return_url),
            prompt=_clean_prompt(prompt),
        )
        await self.store.save_flow(flow, self.settings.auth_flow_state_ttl_seconds)
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id or "",
            "redirect_uri": self.settings.auth_callback_url(),
            "scope": " ".join(self.settings.oidc_scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": _code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        if flow.prompt is not None:
            params["prompt"] = flow.prompt
        return f"{authorization_endpoint}?{urlencode(params)}"

    async def handle_callback(self, params: Mapping[str, str]) -> AuthCallbackResult:
        state = params.get("state")
        code = params.get("code")
        if params.get("error"):
            description = params.get("error_description") or params["error"]
            raise AuthenticationRequired(description)
        if not state or not code:
            raise AuthenticationRequired("OIDC callback is missing state or code")
        flow = await self.store.pop_flow(state)
        if flow is None:
            raise AuthenticationRequired("OIDC state is invalid or expired")

        metadata = await self._provider_metadata()
        token = await self._exchange_code(metadata, code, flow.code_verifier)
        claims = await self._validated_id_token_claims(metadata, token, flow.nonce)
        user = self._user_from_claims(claims, token)
        session = await self.store.save_session(
            session_id=secrets.token_urlsafe(48),
            user=user,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self.settings.auth_session_ttl_seconds),
            token_material=token,
            ttl_seconds=self.settings.auth_session_ttl_seconds,
        )
        return AuthCallbackResult(session=session, redirect_url=flow.return_url)

    async def logout(self, session_id: str | None) -> AuthLogoutResult:
        redirect_url = self.settings.frontend_logged_out_url()
        token_material: dict[str, object] | None = None
        if session_id is not None:
            record = await self.store.get_session(session_id)
            if record is not None:
                token_material = record.token_material
            await self.store.delete_session(session_id)
        end_session_url = await self._end_session_url(token_material)
        return AuthLogoutResult(
            success=True,
            message="signed out",
            redirect_url=end_session_url or redirect_url,
        )

    async def _provider_metadata(self) -> dict[str, object]:
        if self._metadata is not None:
            return self._metadata
        issuer = (self.settings.oidc_issuer_url or "").rstrip("/")
        url = f"{issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderConfigurationError("OIDC provider metadata is unavailable") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderConfigurationError("OIDC provider metadata must be a JSON object")
        self._metadata = cast(dict[str, object], payload)
        return self._metadata

    async def _exchange_code(
        self,
        metadata: Mapping[str, object],
        code: str,
        code_verifier: str,
    ) -> dict[str, object]:
        token_endpoint = _required_url(metadata, "token_endpoint")
        async with AsyncOAuth2Client(
            client_id=self.settings.oidc_client_id,
            client_secret=self.settings.oidc_client_secret,
            redirect_uri=self.settings.auth_callback_url(),
        ) as client:
            try:
                token = await client.fetch_token(
                    token_endpoint,
                    code=code,
                    code_verifier=code_verifier,
                    grant_type="authorization_code",
                )
            except Exception as exc:
                raise AuthenticationRequired("OIDC code exchange failed") from exc
        return dict(token)

    async def _validated_id_token_claims(
        self,
        metadata: Mapping[str, object],
        token: Mapping[str, object],
        nonce: str,
    ) -> dict[str, object]:
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise AuthenticationRequired("OIDC token response did not include an ID token")
        jwks = await self._provider_jwks(metadata)
        key_set = JsonWebKey.import_key_set(jwks)
        jwt = JsonWebToken(_SIGNING_ALGORITHMS)
        try:
            claims = jwt.decode(id_token, key_set)
            claims.validate(leeway=60)
        except JoseError as exc:
            raise AuthenticationRequired("OIDC ID token validation failed") from exc
        payload = dict(claims)
        issuer = str(metadata.get("issuer") or self.settings.oidc_issuer_url or "").rstrip("/")
        if str(payload.get("iss", "")).rstrip("/") != issuer:
            raise AuthenticationRequired("OIDC ID token issuer is invalid")
        if not _audience_matches(payload.get("aud"), self.settings.oidc_client_id or ""):
            raise AuthenticationRequired("OIDC ID token audience is invalid")
        if payload.get("nonce") != nonce:
            raise AuthenticationRequired("OIDC ID token nonce is invalid")
        return payload

    async def _provider_jwks(self, metadata: Mapping[str, object]) -> dict[str, object]:
        if self._jwks is not None:
            return self._jwks
        jwks_uri = _required_url(metadata, "jwks_uri")
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(jwks_uri)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderConfigurationError("OIDC JWKS is unavailable") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderConfigurationError("OIDC JWKS must be a JSON object")
        self._jwks = cast(dict[str, object], payload)
        return self._jwks

    async def _end_session_url(self, token_material: Mapping[str, object] | None) -> str | None:
        try:
            metadata = await self._provider_metadata()
        except ProviderConfigurationError:
            return None
        endpoint = metadata.get("end_session_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            return None
        params: dict[str, str] = {
            "post_logout_redirect_uri": self.settings.frontend_logged_out_url(),
            "client_id": self.settings.oidc_client_id or "",
        }
        id_token = token_material.get("id_token") if token_material is not None else None
        if isinstance(id_token, str) and id_token:
            params["id_token_hint"] = id_token
        return f"{endpoint}?{urlencode(params)}"

    def _user_from_claims(
        self,
        claims: Mapping[str, object],
        token: Mapping[str, object],
    ) -> AuthenticatedUser:
        roles = map_oidc_roles(
            claims,
            client_id=self.settings.oidc_client_id or "",
            claim_paths=self.settings.oidc_role_claim_paths,
            role_map=self.settings.oidc_role_map,
        )
        scopes = _token_scopes(token)
        return AuthenticatedUser(
            tenant_id=self.settings.tenant_id,
            subject=str(claims.get("sub") or ""),
            roles=roles,
            scopes=frozenset(scopes),
            username=_first_string(claims, "preferred_username", "nickname", "email"),
            email=_first_string(claims, "email"),
            name=_first_string(claims, "name", "given_name"),
            token_expires_at=_token_expires_at(token, claims),
        )


def _required_url(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ProviderConfigurationError(f"OIDC provider metadata is missing {key}")
    return value


def _clean_prompt(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _audience_matches(value: object, client_id: str) -> bool:
    if isinstance(value, str):
        return value == client_id
    if isinstance(value, list | tuple | set):
        return client_id in {str(item) for item in value}
    return False


def _token_scopes(token: Mapping[str, object]) -> set[str]:
    raw_scope = token.get("scope")
    if isinstance(raw_scope, str):
        return {item for item in raw_scope.split() if item}
    return set()


def _token_expires_at(
    token: Mapping[str, object],
    claims: Mapping[str, object],
) -> datetime | None:
    expires_at = token.get("expires_at") or claims.get("exp")
    if isinstance(expires_at, int | float):
        return datetime.fromtimestamp(expires_at, UTC)
    return None


def _first_string(claims: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
