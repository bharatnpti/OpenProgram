from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from core.domain.graph import EntityRef
from core.domain.rollup import Rag
from core.domain.status import StatusSource


class RiskRuleId(StrEnum):
    FEATURE_NO_PR = "feature_no_pr"
    PR_AGE = "pr_age"
    STALE_WORK_ITEM = "stale_work_item"


class DriftFindingKind(StrEnum):
    """Continuous stated-vs-actual divergence ("watermelon") findings.

    Each kind compares a developer-stated status against hard signals
    (issue-tracker state + Git/PR facts). Distinct from :class:`RiskRuleId`,
    which scores signal-only staleness/age without a stated-status contrast.
    """

    SAID_DONE_NO_PR = "said_done_no_pr"
    CLAIMED_PROGRESS_NO_ACTIVITY = "claimed_progress_no_activity"
    GREEN_OVER_RED = "green_over_red"


class RiskFindingStatus(StrEnum):
    OPEN = "open"
    CLEARED = "cleared"


@dataclass(frozen=True, kw_only=True)
class RiskEvidence:
    identifier: str
    url: str | None = None
    url_is_user_supplied: bool = False


@dataclass(frozen=True, kw_only=True)
class RiskFinding:
    """A deterministic, signal-derived risk finding.

    Produced entirely from existing facts/graph state with no human input.
    Always shown alongside the owner's human-reported status rather than
    overriding rollup, so divergence (human green, signal risk) is visible
    as a watermelon rather than silently changing RAG.
    """

    tenant_id: str
    rule_id: RiskRuleId
    severity: Rag
    entity_ref: EntityRef
    workstream_id: str | None
    reason: str
    evidence: RiskEvidence
    age_days: int
    detected_at: datetime
    status: RiskFindingStatus
    owner_id: str | None = None
    owner_status_summary: str | None = None
    owner_status_source: StatusSource | None = None
    owner_status_as_of: date | None = None
    owner_status_has_blockers: bool = False


@dataclass(frozen=True, kw_only=True)
class DriftFinding:
    """A continuous drift ("watermelon") finding.

    Produced by contrasting the owner's own stated status/confidence against
    hard signals already in the graph and fact store. Provider-neutral: carries
    no raw reply/DM content, only derived, sanitised fields safe for persona
    views. Never overrides rollup RAG; shown alongside the stated status so the
    divergence is explicit.
    """

    tenant_id: str
    kind: DriftFindingKind
    severity: Rag
    entity_ref: EntityRef
    workstream_id: str | None
    reason: str
    detected_at: datetime
    owner_id: str | None = None
    stated_source: StatusSource | None = None
    evidence: RiskEvidence | None = None
    child_entity_ref: EntityRef | None = None


@dataclass(frozen=True, kw_only=True)
class RiskThresholds:
    """Per-workstream thresholds; falls back to global defaults when unset."""

    feature_no_pr_days: int
    pr_age_days: int
    stale_days: int


@dataclass(frozen=True, kw_only=True)
class RiskProviderConfig:
    """Plain provider config values RiskService needs, decoupled from Settings.

    core/ must not import config.settings directly (enforced by import-linter),
    so callers in api/infra pass these primitives through instead of the
    Settings object itself.
    """

    jira_base_url: str | None = None
    github_base_url: str = "https://api.github.com"
    default_no_pr_days: int = 3
    default_pr_age_days: int = 3
    default_stale_days: int = 7
    # Drift/watermelon detection knobs.
    default_no_activity_days: int = 3
    done_states: tuple[str, ...] = ("done", "closed", "resolved", "merged")
