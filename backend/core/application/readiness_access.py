"""Who reads and acts on release readiness, and how far: the one rule every readiness read keeps.

The readiness routes (api/routers/readiness.py) and Ask's release_readiness
tool both decide with these, so Ask never reads a board the asker's own request
would be refused, and a refusal reads the same in both.

Reading a project's board takes read_project_progress (product owner, manager,
executive, admin), or a scrum master of a pod working on the project; a
developer has nothing to act on and reads none. A pod's board is for its scrum
master, a manager and an admin. An executive reads the rows without drafts or
pods' rows. The console's ``readsReadiness`` (frontend-v3/src/app/access.ts)
mirrors ``project_viewer``: change both together.
"""

from __future__ import annotations

from datetime import date

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import PROJECT_OUTSIDE_SCOPE
from core.application.forecast_service import ForecastService
from core.application.release_readiness_service import ReleaseReadinessService, Viewer
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied
from core.domain.release_readiness import ScopeKind, ScopeRef

NOT_A_READER = "Release readiness is read by the people who decide on a release."
NOT_YOURS = "You can act on readiness only for pods you run and the projects they work on."


def reaches_every_scope(principal: Principal) -> bool:
    """Reaches every project and pod: an admin, a manager or a product owner."""
    return any(principal.has_role(role) for role in (Role.ADMIN, Role.MGR, Role.PO))


def reads_some_board(principal: Principal) -> bool:
    """Whether a project's board answers the principal somewhere: everywhere by role,
    or a scrum master's for the projects their pods work on."""
    return (
        reaches_every_scope(principal)
        or principal.has_role(Role.SM)
        or AuthorizationPolicy().can(principal, Capability.READ_PROJECT_PROGRESS)
    )


async def project_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    project_id: str,
    *,
    reading: bool,
) -> Viewer:
    """How far the principal reaches on a project's board; AuthorizationDenied in words."""
    policy = AuthorizationPolicy()
    may_act = policy.can(principal, Capability.ACT_ON_READINESS)
    if reaches_every_scope(principal):
        return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
    if principal.has_role(Role.SM):
        runs = await forecasts.runs_project_pod(principal.tenant_id, project_id, principal.subject)
        if runs:
            pods = await service.pods_run_by(principal.tenant_id, project_id, principal.subject)
            return Viewer(
                subject=principal.subject,
                roles=principal.roles,
                may_act=may_act,
                pods=frozenset(pods),
                project_reach=True,
            )
    if reading and policy.can(principal, Capability.READ_PROJECT_PROGRESS):
        # An executive reads the project's and releases' rows; nothing to act on,
        # no drafts, and no pod's rows.
        return Viewer(
            subject=principal.subject,
            roles=principal.roles,
            may_act=False,
            pods=frozenset(),
            project_reach=False,
            sees_drafts=False,
        )
    if reading and principal.has_role(Role.SM):
        # A read, in the words of every scoped project read (delivery_scope).
        raise AuthorizationDenied(PROJECT_OUTSIDE_SCOPE)
    raise AuthorizationDenied(NOT_YOURS if may_act else NOT_A_READER)


async def pod_viewer(principal: Principal, forecasts: ForecastService, pod_id: str) -> Viewer:
    """How far the principal reaches on a pod's board; AuthorizationDenied in words."""
    may_act = AuthorizationPolicy().can(principal, Capability.ACT_ON_READINESS)
    if principal.has_role(Role.ADMIN) or principal.has_role(Role.MGR):
        return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
    if principal.has_role(Role.SM) and await forecasts.runs_pod(
        principal.tenant_id, pod_id, principal.subject, date.today()
    ):
        return Viewer(
            subject=principal.subject,
            roles=principal.roles,
            may_act=may_act,
            pods=frozenset({pod_id}),
            project_reach=False,
        )
    raise AuthorizationDenied(NOT_YOURS)


async def scope_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    scope: ScopeRef,
    project_id: str | None,
    *,
    reading: bool,
) -> Viewer:
    """How far the principal reaches on one finding's or draft's scope."""
    if scope.kind is ScopeKind.POD:
        if reaches_every_scope(principal):
            may_act = AuthorizationPolicy().can(principal, Capability.ACT_ON_READINESS)
            return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
        return await pod_viewer(principal, forecasts, scope.id)
    return await project_viewer(principal, forecasts, service, project_id or "", reading=reading)
