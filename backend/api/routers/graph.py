from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_current_principal, get_graph_query_service
from api.dtos import GraphTreeDto
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.graph_queries import GraphQueryService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/programs/{program_id}/tree", response_model=GraphTreeDto)
async def program_tree(
    program_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    graph_service: Annotated[GraphQueryService, Depends(get_graph_query_service)],
) -> GraphTreeDto:
    # `ensure` raises AuthorizationDenied, which is not an HTTPException: left
    # uncaught it escapes as a 500, so a role that simply may not read the tree
    # was told the server had failed. Every other router converts it here.
    try:
        AuthorizationPolicy().ensure(principal, Capability.READ_TEAM_AGGREGATE)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    tree = await graph_service.program_tree(principal.tenant_id, program_id, as_of)
    return GraphTreeDto.from_domain(tree)
