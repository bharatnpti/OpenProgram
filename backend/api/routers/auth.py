from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from api.dependencies import auth_login_url_for_request, get_registry, get_settings_from_request
from api.dtos import AuthStatusResponse, AuthUserResponse, LogoutResponse
from config.settings import Settings
from core.domain.errors import AuthenticationRequired, ProviderConfigurationError
from core.ports.auth import AuthCredentials, AuthenticatedUser
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    authorization: Annotated[str | None, Header()] = None,
) -> AuthStatusResponse:
    _no_store(response)
    credentials = AuthCredentials(
        authorization=authorization,
        session_id=request.cookies.get(settings.auth_cookie_name),
    )
    if settings.auth_provider == "dev":
        principal = await registry.current_principal(credentials).get()
        user = AuthenticatedUser(
            tenant_id=principal.tenant_id,
            subject=principal.subject,
            roles=principal.roles,
            scopes=principal.scopes,
        )
        return AuthStatusResponse(
            authenticated=True,
            provider=settings.auth_provider,
            user=AuthUserResponse.from_user(user),
        )

    session = await registry.auth_session(credentials.session_id)
    if session is None:
        return AuthStatusResponse(
            authenticated=False,
            provider=settings.auth_provider,
            login_url=auth_login_url_for_request(request, settings),
            message="authentication required",
        )
    return AuthStatusResponse(
        authenticated=True,
        provider=settings.auth_provider,
        user=AuthUserResponse.from_user(session.user),
    )


@router.get("/login")
async def auth_login(
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    return_url: Annotated[str | None, Query()] = None,
    prompt: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if settings.auth_provider == "dev":
        return RedirectResponse(settings.safe_auth_return_url(return_url), status_code=302)
    try:
        redirect_url = await registry.begin_auth_login(return_url, prompt)
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/callback")
async def auth_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> RedirectResponse:
    if settings.auth_provider == "dev":
        return RedirectResponse(settings.auth_frontend_url, status_code=302)
    try:
        result = await registry.complete_auth_callback(dict(request.query_params))
    except AuthenticationRequired as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    response = RedirectResponse(result.redirect_url, status_code=302)
    _set_session_cookies(response, settings, result.session.session_id, result.session.csrf_token)
    return response


@router.post("/logout", response_model=LogoutResponse)
async def auth_logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> LogoutResponse:
    _no_store(response)
    result = await registry.logout_auth_session(request.cookies.get(settings.auth_cookie_name))
    _expire_auth_cookies(response, settings)
    return LogoutResponse(
        success=result.success,
        message=result.message,
        redirect_url=result.redirect_url,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _set_session_cookies(
    response: Response,
    settings: Settings,
    session_id: str,
    csrf_token: str,
) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        session_id,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _expire_auth_cookies(response: Response, settings: Settings) -> None:
    for cookie_name, httponly in (
        (settings.auth_cookie_name, True),
        (settings.auth_csrf_cookie_name, False),
    ):
        response.set_cookie(
            cookie_name,
            "",
            max_age=0,
            expires=0,
            httponly=httponly,
            secure=settings.auth_cookie_secure,
            samesite=settings.auth_cookie_samesite,
            path="/",
        )
