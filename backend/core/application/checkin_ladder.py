"""Who reads what of a pod's check-ins beyond each person's state (D87).

The silence ladder of the day (when each person was asked and replied, and the
rungs that went out: ``core.domain.checkin_rungs``) is for the roles that
already read a pod's check-ins: a scrum master for their own pods, a manager,
an admin. A person's own check-in time and zone are for an admin and a scrum
master of their own pods only. Someone who reads their own pod without the pod
check-in capability (a developer) gets the pod's counts and their own row.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import DeliveryScopeService
from core.domain.auth import Principal, Role


@dataclass(frozen=True, kw_only=True)
class PodCheckinReads:
    """What of a pod's check-ins the caller reads beyond the state of each person."""

    #: When each person was asked and replied, and the rungs that went out.
    ladder: bool = False
    #: Each person's check-in time and zone.
    schedule: bool = False
    #: Only this person's own row (a developer on their own pod); None for every row.
    only: str | None = None


async def pod_checkin_reads(
    scope: DeliveryScopeService, principal: Principal, pod_id: str
) -> PodCheckinReads:
    """What the caller reads of the pod's check-ins; the route has already let them in.

    The ladder: a manager or admin for any pod, a scrum master for the pods
    they belong to or run. The schedule: an admin, and a scrum master of their
    own pods. A reader without ``READ_POD_CHECKINS`` reads their own row only.
    """
    policy = AuthorizationPolicy()
    if not policy.can(principal, Capability.READ_POD_CHECKINS):
        return PodCheckinReads(only=principal.subject)
    admin = principal.has_role(Role.ADMIN)
    manager = principal.has_role(Role.MGR)
    own = False
    if principal.has_role(Role.SM):
        own = pod_id in (await scope.scope_of(principal.tenant_id, principal.subject)).pods
    return PodCheckinReads(ladder=admin or manager or own, schedule=admin or own)
