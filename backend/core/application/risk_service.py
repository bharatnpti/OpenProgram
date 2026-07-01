from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from core.application.proof_links import pull_request_evidence
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    JsonScalar,
    NodeKind,
)
from core.domain.risk import (
    RiskEvidence,
    RiskFinding,
    RiskFindingStatus,
    RiskProviderConfig,
    RiskRuleId,
    RiskThresholds,
)
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus
from core.ports.repositories import GraphRepository, StatusRepository, TimeSeriesRepository

ACTIVE_WORK_ITEM_STATES = {"proposed", "in_progress", "in_review", "blocked"}
RISK_FACT_SOURCE = "risk"
_RISK_FACT_SCAN_LIMIT = 5000


@dataclass(frozen=True, kw_only=True)
class RiskAssessmentDelta:
    project_id: str
    as_of: date
    open_findings: tuple[RiskFinding, ...]
    newly_opened: tuple[RiskFinding, ...]
    newly_cleared: tuple[RiskFinding, ...]


class RiskService:
    """Deterministic, signal-derived risk detection.

    Reads only facts and graph state already produced by the existing
    (polled) sync/fact pipeline -- no new ingestion. Findings are always
    shown next to the owner's own human-reported status rather than
    changing rollup RAG.
    """

    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        status_repository: StatusRepository,
        provider_config: RiskProviderConfig | None = None,
    ) -> None:
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._status_repository = status_repository
        self._provider_config = provider_config or RiskProviderConfig()

    # ---- read APIs (risk board) -----------------------------------------

    async def project_risks(
        self, tenant_id: str, project_id: str, as_of: date
    ) -> list[RiskFinding]:
        await self._ensure_project(tenant_id, project_id)
        return await self._load_open_findings(tenant_id, project_id=project_id, as_of=as_of)

    async def portfolio_risks(self, tenant_id: str, as_of: date) -> list[RiskFinding]:
        return await self._load_open_findings(tenant_id, project_id=None, as_of=as_of)

    # ---- assessment + persistence (per-project EOD workflow) -----------

    async def assess_and_persist_project(
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
    ) -> RiskAssessmentDelta:
        await self._ensure_project(tenant_id, project_id)
        current = await self._assess_project(tenant_id, project_id, as_of)
        previous = await self._load_open_findings(tenant_id, project_id=project_id, as_of=as_of)
        return await self._persist_delta(tenant_id, project_id, previous, current, as_of)

    # ---- assessment (pure scoring) ---------------------------------------

    async def _assess_project(  # noqa: C901
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
    ) -> list[RiskFinding]:
        nodes = await self._graph_repository.list_nodes(tenant_id)
        nodes_by_id = {node.id: node for node in nodes}
        edges = await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS)

        workstream_ids = [
            edge.to_node_id
            for edge in edges
            if edge.from_node_id == project_id
            and nodes_by_id.get(edge.to_node_id) is not None
            and nodes_by_id[edge.to_node_id].kind is NodeKind.WORKSTREAM
        ]

        owner_cache: dict[str, DeveloperStatus | None] = {}
        thresholds_by_workstream_id: dict[str | None, RiskThresholds] = {
            None: self._default_thresholds()
        }
        workstream_by_repo_and_pr: dict[
            tuple[str, str], tuple[GraphNode | None, GraphNode | None]
        ] = {}
        findings: list[RiskFinding] = []

        for workstream_id in workstream_ids:
            workstream = nodes_by_id.get(workstream_id)
            thresholds = self._thresholds_for_workstream(workstream)
            thresholds_by_workstream_id[workstream_id] = thresholds
            work_items = [
                nodes_by_id[edge.to_node_id]
                for edge in edges
                if edge.from_node_id == workstream_id
                and nodes_by_id.get(edge.to_node_id) is not None
                and nodes_by_id[edge.to_node_id].kind is NodeKind.WORK_ITEM
            ]
            findings.extend(
                await self._work_item_findings(
                    tenant_id, workstream, work_items, thresholds, as_of, owner_cache
                )
            )
            for item in work_items:
                repo = _string_metadata(item, "repo")
                pr_id = _string_metadata(item, "pr_id")
                if repo and pr_id:
                    workstream_by_repo_and_pr[(repo, pr_id)] = (item, workstream)

        repo_scope = self._project_repo_scope(project_id, nodes_by_id, edges)
        findings.extend(
            await self._pr_age_findings(
                tenant_id,
                repo_scope,
                workstream_by_repo_and_pr,
                thresholds_by_workstream_id,
                as_of,
                owner_cache,
            )
        )
        return findings

    async def _work_item_findings(
        self,
        tenant_id: str,
        workstream: GraphNode | None,
        work_items: list[GraphNode],
        thresholds: RiskThresholds,
        as_of: date,
        owner_cache: dict[str, DeveloperStatus | None],
    ) -> list[RiskFinding]:
        findings: list[RiskFinding] = []
        for item in work_items:
            state = _string_metadata(item, "state") or "proposed"
            if state not in ACTIVE_WORK_ITEM_STATES:
                continue
            item_type = _string_metadata(item, "item_type") or "feature"
            pr_id = _string_metadata(item, "pr_id")
            reference_at = _datetime_metadata(item, "last_transition_at") or _datetime_metadata(
                item, "created_at"
            )
            age_days = _age_days(reference_at, as_of)
            if age_days is None:
                continue
            owner_id = _string_metadata(item, "owner_id")

            if item_type == "feature" and not pr_id and age_days >= thresholds.feature_no_pr_days:
                findings.append(
                    await self._build_finding(
                        tenant_id=tenant_id,
                        rule_id=RiskRuleId.FEATURE_NO_PR,
                        severity=_severity_for_age(age_days, thresholds.feature_no_pr_days),
                        entity_ref=item.ref,
                        workstream_id=workstream.id if workstream is not None else None,
                        reason=(
                            f"{item.name} has been active for {age_days} day(s) "
                            "with no linked pull request."
                        ),
                        evidence=RiskEvidence(identifier=item.id),
                        age_days=age_days,
                        owner_id=owner_id,
                        as_of=as_of,
                        owner_cache=owner_cache,
                    )
                )
            if age_days >= thresholds.stale_days:
                findings.append(
                    await self._build_finding(
                        tenant_id=tenant_id,
                        rule_id=RiskRuleId.STALE_WORK_ITEM,
                        severity=_severity_for_age(age_days, thresholds.stale_days),
                        entity_ref=item.ref,
                        workstream_id=workstream.id if workstream is not None else None,
                        reason=f"{item.name} has had no state change in {age_days} day(s).",
                        evidence=RiskEvidence(identifier=item.id),
                        age_days=age_days,
                        owner_id=owner_id,
                        as_of=as_of,
                        owner_cache=owner_cache,
                    )
                )
        return findings

    async def _pr_age_findings(
        self,
        tenant_id: str,
        repo_scope: set[str],
        workstream_by_repo_and_pr: Mapping[
            tuple[str, str], tuple[GraphNode | None, GraphNode | None]
        ],
        thresholds_by_workstream_id: Mapping[str | None, RiskThresholds],
        as_of: date,
        owner_cache: dict[str, DeveloperStatus | None],
    ) -> list[RiskFinding]:
        if not repo_scope:
            return []
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id, sources=("vcs_pull_request",), limit=_RISK_FACT_SCAN_LIMIT
        )
        latest_by_pr: dict[tuple[str, str], FactEvent] = {}
        for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
            repo = _payload_str(fact.payload, "repo")
            pr_id = _payload_str(fact.payload, "id")
            if repo is None or pr_id is None or repo not in repo_scope:
                continue
            latest_by_pr[(repo, pr_id)] = fact

        findings: list[RiskFinding] = []
        for (repo, pr_id), fact in latest_by_pr.items():
            if _payload_bool(fact.payload, "merged"):
                continue
            opened_at = _payload_datetime(fact.payload, "opened_at") or fact.observed_at
            age_days = _age_days(opened_at, as_of)
            if age_days is None:
                continue
            work_item, workstream = workstream_by_repo_and_pr.get((repo, pr_id), (None, None))
            thresholds = thresholds_by_workstream_id.get(
                workstream.id if workstream is not None else None,
                thresholds_by_workstream_id[None],
            )
            if age_days < thresholds.pr_age_days:
                continue
            owner_id = (
                _string_metadata(work_item, "owner_id") if work_item is not None else None
            ) or fact.entity_ref.id
            title = _payload_str(fact.payload, "title") or f"PR {pr_id}"
            entity_ref = work_item.ref if work_item is not None else fact.entity_ref
            findings.append(
                await self._build_finding(
                    tenant_id=tenant_id,
                    rule_id=RiskRuleId.PR_AGE,
                    severity=_severity_for_age(age_days, thresholds.pr_age_days),
                    entity_ref=entity_ref,
                    workstream_id=workstream.id if workstream is not None else None,
                    reason=f"Pull request '{title}' in {repo} has been open for {age_days} day(s).",
                    evidence=pull_request_evidence(
                        repo=repo,
                        pr_id=pr_id,
                        github_base_url=self._provider_config.github_base_url,
                        metadata=work_item.metadata if work_item is not None else None,
                    ),
                    age_days=age_days,
                    owner_id=owner_id,
                    as_of=as_of,
                    owner_cache=owner_cache,
                )
            )
        return findings

    async def _build_finding(
        self,
        *,
        tenant_id: str,
        rule_id: RiskRuleId,
        severity: Rag,
        entity_ref: EntityRef,
        workstream_id: str | None,
        reason: str,
        evidence: RiskEvidence,
        age_days: int,
        owner_id: str | None,
        as_of: date,
        owner_cache: dict[str, DeveloperStatus | None],
    ) -> RiskFinding:
        owner_status = await self._owner_status(tenant_id, owner_id, as_of, owner_cache)
        return RiskFinding(
            tenant_id=tenant_id,
            rule_id=rule_id,
            severity=severity,
            entity_ref=entity_ref,
            workstream_id=workstream_id,
            reason=reason,
            evidence=evidence,
            age_days=age_days,
            detected_at=datetime.now(tz=UTC),
            status=RiskFindingStatus.OPEN,
            owner_id=owner_id,
            owner_status_summary=owner_status.summary if owner_status is not None else None,
            owner_status_source=owner_status.source if owner_status is not None else None,
            owner_status_as_of=owner_status.as_of if owner_status is not None else None,
            owner_status_has_blockers=(
                bool(owner_status.blockers) if owner_status is not None else False
            ),
        )

    async def _owner_status(
        self,
        tenant_id: str,
        owner_id: str | None,
        as_of: date,
        owner_cache: dict[str, DeveloperStatus | None],
    ) -> DeveloperStatus | None:
        if owner_id is None:
            return None
        if owner_id not in owner_cache:
            owner_cache[owner_id] = await self._status_repository.latest_developer_status(
                tenant_id, owner_id, as_of
            )
        return owner_cache[owner_id]

    def _default_thresholds(self) -> RiskThresholds:
        return RiskThresholds(
            feature_no_pr_days=self._provider_config.default_no_pr_days,
            pr_age_days=self._provider_config.default_pr_age_days,
            stale_days=self._provider_config.default_stale_days,
        )

    def _thresholds_for_workstream(self, workstream: GraphNode | None) -> RiskThresholds:
        if workstream is None:
            return self._default_thresholds()
        metadata = workstream.metadata
        return RiskThresholds(
            feature_no_pr_days=_int_metadata(metadata, "risk_no_pr_days")
            or self._provider_config.default_no_pr_days,
            pr_age_days=_int_metadata(metadata, "risk_pr_age_days")
            or self._provider_config.default_pr_age_days,
            stale_days=_int_metadata(metadata, "risk_stale_days")
            or self._provider_config.default_stale_days,
        )

    def _project_repo_scope(
        self,
        project_id: str,
        nodes_by_id: Mapping[str, GraphNode],
        edges: Sequence[GraphEdge],
    ) -> set[str]:
        project = nodes_by_id.get(project_id)
        repos = set(_parse_repo_list(project.metadata.get("github_repos"))) if project else set()
        for edge in edges:
            if edge.from_node_id != project_id:
                continue
            pod = nodes_by_id.get(edge.to_node_id)
            if pod is not None and pod.kind is NodeKind.POD:
                repos.update(_parse_repo_list(pod.metadata.get("github_repos")))
        return repos

    # ---- persisted risk facts (open/cleared deltas) ----------------------

    async def _persist_delta(
        self,
        tenant_id: str,
        project_id: str,
        previous: list[RiskFinding],
        current: list[RiskFinding],
        as_of: date,
    ) -> RiskAssessmentDelta:
        previous_by_key = {_risk_key(finding): finding for finding in previous}
        current_by_key = {_risk_key(finding): finding for finding in current}

        newly_opened = [
            finding for key, finding in current_by_key.items() if key not in previous_by_key
        ]
        newly_cleared = [
            replace(previous_by_key[key], status=RiskFindingStatus.CLEARED)
            for key in previous_by_key
            if key not in current_by_key
        ]

        for finding in newly_opened:
            await self._append_risk_fact(tenant_id, project_id, finding, as_of, transition="opened")
        for finding in newly_cleared:
            await self._append_risk_fact(
                tenant_id, project_id, finding, as_of, transition="cleared"
            )

        return RiskAssessmentDelta(
            project_id=project_id,
            as_of=as_of,
            open_findings=tuple(current),
            newly_opened=tuple(newly_opened),
            newly_cleared=tuple(newly_cleared),
        )

    async def _append_risk_fact(
        self,
        tenant_id: str,
        project_id: str,
        finding: RiskFinding,
        as_of: date,
        *,
        transition: str,
    ) -> None:
        risk_key = _risk_key(finding)
        # Owned findings target the developer entity ref so the existing,
        # already-polled daily check-in context (StatusCollector.build_context)
        # naturally surfaces this risk next time that developer is asked for
        # status -- no new dispatch path or event-driven trigger is added.
        entity_ref = (
            replace(finding.entity_ref, kind=NodeKind.DEVELOPER, id=finding.owner_id)
            if finding.owner_id is not None
            else finding.entity_ref
        )
        payload: dict[str, JsonScalar] = {
            "risk_key": risk_key,
            "rule_id": finding.rule_id.value,
            "severity": finding.severity.value,
            "entity_kind": finding.entity_ref.kind.value,
            "entity_id": finding.entity_ref.id,
            "workstream_id": finding.workstream_id,
            "project_id": project_id,
            "reason": finding.reason,
            "evidence_identifier": finding.evidence.identifier,
            "evidence_url": finding.evidence.url,
            "evidence_url_is_user_supplied": finding.evidence.url_is_user_supplied,
            "age_days": finding.age_days,
            "owner_id": finding.owner_id,
            "detected_at": finding.detected_at.isoformat(),
            "transition": transition,
        }
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=tenant_id,
                source=RISK_FACT_SOURCE,
                entity_ref=entity_ref,
                payload=payload,
                observed_at=datetime.now(tz=UTC),
                correlation_id=f"risk:{tenant_id}:{risk_key}:{transition}:{as_of.isoformat()}",
            )
        )

    async def _load_open_findings(
        self,
        tenant_id: str,
        *,
        project_id: str | None,
        as_of: date,
    ) -> list[RiskFinding]:
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id, sources=(RISK_FACT_SOURCE,), limit=_RISK_FACT_SCAN_LIMIT
        )
        if project_id is not None:
            facts = [
                fact for fact in facts if _payload_str(fact.payload, "project_id") == project_id
            ]
        latest_by_key: dict[str, FactEvent] = {}
        for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
            key = _payload_str(fact.payload, "risk_key")
            if key is None:
                continue
            latest_by_key[key] = fact

        owner_cache: dict[str, DeveloperStatus | None] = {}
        findings: list[RiskFinding] = []
        for fact in latest_by_key.values():
            if _payload_str(fact.payload, "transition") != "opened":
                continue
            finding = await self._finding_from_fact(tenant_id, fact, as_of, owner_cache)
            if finding is not None:
                findings.append(finding)
        return sorted(
            findings,
            key=lambda finding: (_SEVERITY_RANK[finding.severity], finding.age_days),
        )

    async def _finding_from_fact(
        self,
        tenant_id: str,
        fact: FactEvent,
        as_of: date,
        owner_cache: dict[str, DeveloperStatus | None],
    ) -> RiskFinding | None:
        rule_id_value = _payload_str(fact.payload, "rule_id")
        entity_kind_value = _payload_str(fact.payload, "entity_kind")
        entity_id = _payload_str(fact.payload, "entity_id")
        reason = _payload_str(fact.payload, "reason")
        identifier = _payload_str(fact.payload, "evidence_identifier")
        detected_at_value = _payload_str(fact.payload, "detected_at")
        if not (rule_id_value and entity_kind_value and entity_id and reason and identifier):
            return None
        try:
            rule_id = RiskRuleId(rule_id_value)
            entity_kind = NodeKind(entity_kind_value)
        except ValueError:
            return None
        severity = _rag_from_payload(_payload_str(fact.payload, "severity"))
        detected_at = _optional_datetime(detected_at_value) or fact.observed_at
        owner_id = _payload_str(fact.payload, "owner_id")
        owner_status = await self._owner_status(tenant_id, owner_id, as_of, owner_cache)
        current_age_days = max(0, (as_of - detected_at.date()).days) + (
            _payload_int(fact.payload, "age_days") or 0
        )
        return RiskFinding(
            tenant_id=tenant_id,
            rule_id=rule_id,
            severity=severity,
            entity_ref=EntityRef(tenant_id=tenant_id, kind=entity_kind, id=entity_id),
            workstream_id=_payload_str(fact.payload, "workstream_id"),
            reason=reason,
            evidence=RiskEvidence(
                identifier=identifier,
                url=_payload_str(fact.payload, "evidence_url"),
                url_is_user_supplied=bool(
                    _payload_bool(fact.payload, "evidence_url_is_user_supplied")
                ),
            ),
            age_days=current_age_days,
            detected_at=detected_at,
            status=RiskFindingStatus.OPEN,
            owner_id=owner_id,
            owner_status_summary=owner_status.summary if owner_status is not None else None,
            owner_status_source=owner_status.source if owner_status is not None else None,
            owner_status_as_of=owner_status.as_of if owner_status is not None else None,
            owner_status_has_blockers=(
                bool(owner_status.blockers) if owner_status is not None else False
            ),
        )

    async def _ensure_project(self, tenant_id: str, project_id: str) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, project_id)
        if node is None:
            raise GraphNotFound(f"project {project_id} not found for tenant {tenant_id}")
        if node.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"{project_id} exists as a {node.kind.value}, not a project")
        return node


_SEVERITY_RANK: dict[Rag, int] = {Rag.RED: 0, Rag.AMBER: 1, Rag.UNKNOWN: 2, Rag.GREEN: 3}


def _risk_key(finding: RiskFinding) -> str:
    return f"{finding.rule_id.value}:{finding.entity_ref.kind.value}:{finding.entity_ref.id}"


def _severity_for_age(age_days: int, threshold_days: int) -> Rag:
    if threshold_days <= 0 or age_days >= threshold_days * 2:
        return Rag.RED
    return Rag.AMBER


def _rag_from_payload(value: str | None) -> Rag:
    if value is None:
        return Rag.AMBER
    try:
        return Rag(value)
    except ValueError:
        return Rag.AMBER


def _string_metadata(node: GraphNode | None, key: str) -> str | None:
    if node is None:
        return None
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _int_metadata(metadata: Mapping[str, JsonScalar], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _datetime_metadata(node: GraphNode, key: str) -> datetime | None:
    value = node.metadata.get(key)
    if not isinstance(value, str) or not value:
        return None
    return _optional_datetime(value)


def _optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _age_days(reference_at: datetime | None, as_of: date) -> int | None:
    if reference_at is None:
        return None
    return max(0, (as_of - reference_at.date()).days)


def _parse_repo_list(value: JsonScalar) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in value.replace("\n", ",").split(","):
        item = raw_item.strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items


def _payload_str(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _payload_int(payload: Mapping[str, JsonScalar], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _payload_bool(payload: Mapping[str, JsonScalar], key: str) -> bool | None:
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _payload_datetime(payload: Mapping[str, JsonScalar], key: str) -> datetime | None:
    value = payload.get(key)
    return _optional_datetime(value) if isinstance(value, str) else None
