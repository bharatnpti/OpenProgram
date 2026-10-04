from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.application.person_names import PersonNames, person_name
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.ports.repositories import TimeSeriesRepository

DEFAULT_FEED_LOOKBACK_DAYS = 7
DEFAULT_FEED_LIMIT = 50
DEFAULT_FEED_SOURCES = (
    "work_item",
    "vcs_pull_request",
    "vcs_commit",
    "issue",
    "checkin",
    "risk",
    "cross_person_request",
)


# Canonical cross-person fact keys first, then the legacy aliases older facts carry.
_REPORTER_ID_KEYS = ("reporter_id", "requester_id")
_REPORTER_NAME_KEYS = ("reporter_name", "requester_name")
_REFERENCED_PERSON_ID_KEYS = ("referenced_person_id", "counterpart_id")
_REFERENCED_PERSON_NAME_KEYS = ("referenced_person_name", "counterpart_display_name")


@dataclass(frozen=True, kw_only=True)
class PortfolioFeedItemView:
    source: str
    kind: str
    summary: str
    entity_ref: EntityRef
    observed_at: datetime
    details: Mapping[str, JsonScalar]
    # The person ``entity_ref`` names, when it names one; never a raw chat id.
    person_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class PortfolioFeedView:
    as_of: datetime
    since: datetime
    items: tuple[PortfolioFeedItemView, ...]


class PortfolioFeedService:
    def __init__(
        self,
        time_series_repository: TimeSeriesRepository,
        person_names: PersonNames | None = None,
    ) -> None:
        self._time_series_repository = time_series_repository
        # Without a member or directory source a person reads as the name
        # recorded on the fact, else "a team member": never as their id.
        self._person_names = person_names or PersonNames()

    async def feed(
        self,
        tenant_id: str,
        since: datetime | None = None,
        sources: Sequence[str] | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> PortfolioFeedView:
        as_of = datetime.now(tz=UTC)
        since_at = since or (as_of - timedelta(days=DEFAULT_FEED_LOOKBACK_DAYS))
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id,
            since=since_at,
            sources=sources or DEFAULT_FEED_SOURCES,
            limit=limit,
        )
        ordered = sorted(
            facts,
            key=lambda fact: (fact.observed_at, fact.ingested_at, fact.correlation_id),
            reverse=True,
        )
        names = await self._person_names.resolve(
            tenant_id, (person_id for fact in ordered for person_id in _person_ids(fact))
        )
        items = tuple(
            PortfolioFeedItemView(
                source=fact.source,
                kind=_kind_for_fact(fact),
                summary=_summary_for_fact(fact, names),
                entity_ref=fact.entity_ref,
                observed_at=fact.observed_at,
                details=_details_for_fact(fact, names),
                person_name=_entity_person_name(fact, names),
            )
            for fact in ordered
        )
        return PortfolioFeedView(as_of=as_of, since=since_at, items=items)


def _person_ids(fact: FactEvent) -> tuple[str, ...]:
    """Every person id the fact's feed item shows, the entity's included."""
    ids: list[str] = []
    if fact.entity_ref.kind is NodeKind.DEVELOPER:
        ids.append(fact.entity_ref.id)
    if fact.source == "cross_person_request":
        for keys in (_REPORTER_ID_KEYS, _REFERENCED_PERSON_ID_KEYS):
            person_id = _payload_string_any(fact.payload, keys)
            if person_id is not None:
                ids.append(person_id)
    return tuple(ids)


def _entity_person_name(fact: FactEvent, names: Mapping[str, str]) -> str | None:
    """Who a person entity is, by name; ``None`` when the entity is not a person."""
    if fact.entity_ref.kind is not NodeKind.DEVELOPER:
        return None
    person_id = fact.entity_ref.id
    return person_name(names, person_id, _recorded_name(fact, person_id))


def _recorded_name(fact: FactEvent, person_id: str) -> str | None:
    """The name the fact itself recorded for ``person_id``, if any."""
    payload = fact.payload
    if fact.source == "checkin" and person_id == fact.entity_ref.id:
        return _payload_string(payload, "developer_name")
    if fact.source == "cross_person_request":
        if person_id == _payload_string_any(payload, _REFERENCED_PERSON_ID_KEYS):
            return _payload_string_any(payload, _REFERENCED_PERSON_NAME_KEYS)
        if person_id == _payload_string_any(payload, _REPORTER_ID_KEYS):
            return _payload_string_any(payload, _REPORTER_NAME_KEYS)
    return None


def _kind_for_fact(fact: FactEvent) -> str:
    return {
        "work_item": "work_item_transition",
        "vcs_pull_request": "pull_request_update",
        "vcs_commit": "commit",
        "issue": "issue_update",
        "checkin": "checkin_update",
        "risk": _risk_feed_kind(fact),
        "cross_person_request": _cross_person_feed_kind(fact),
    }.get(fact.source, fact.source)


def _risk_feed_kind(fact: FactEvent) -> str:
    transition = _payload_string(fact.payload, "transition")
    return "risk_cleared" if transition == "cleared" else "risk_opened"


def _cross_person_feed_kind(fact: FactEvent) -> str:
    transition = _payload_string(fact.payload, "transition") or "opened"
    return f"cross_person_request_{transition}"


def _summary_for_fact(fact: FactEvent, names: Mapping[str, str]) -> str:
    payload = fact.payload
    if fact.source == "work_item":
        label = _payload_string(payload, "name") or fact.entity_ref.id
        from_state = _payload_string(payload, "from_state") or "unknown"
        to_state = _payload_string(payload, "to_state") or "updated"
        return f"{label} moved from {from_state} to {to_state}"
    if fact.source == "vcs_pull_request":
        repo = _payload_string(payload, "repo") or "unknown repo"
        pr_id = _payload_string(payload, "id") or "?"
        title = _payload_string(payload, "title") or f"PR {pr_id}"
        merged = _payload_bool(payload, "merged")
        status = "merged" if merged else "updated"
        return f"PR {pr_id} in {repo} {status}: {title}"
    if fact.source == "vcs_commit":
        repo = _payload_string(payload, "repo") or "unknown repo"
        sha = _payload_string(payload, "sha") or "unknown sha"
        message = _payload_string(payload, "message") or "commit"
        return f"Commit {sha[:7]} in {repo}: {message}"
    if fact.source == "issue":
        key = _payload_string(payload, "key") or fact.entity_ref.id
        state = _payload_string(payload, "state") or "updated"
        title = _payload_string(payload, "title") or key
        return f"Issue {key} moved to {state}: {title}"
    if fact.source == "checkin":
        # The member's name, else the directory's, else the one the collector
        # recorded: an older fact recorded none, or only the id.
        developer = person_name(
            names, fact.entity_ref.id, _payload_string(payload, "developer_name")
        )
        status_source = _payload_string(payload, "status_source") or "confirmed"
        blocker_count = _payload_int(payload, "blocker_count") or 0
        eta_change_days = _payload_int(payload, "eta_change_days")
        eta_text = f", eta change {eta_change_days:+d}d" if eta_change_days is not None else ""
        return (
            f"Check-in updated for {developer}: {status_source}, "
            f"{blocker_count} blocker(s){eta_text}"
        )
    if fact.source == "risk":
        return _risk_summary(fact)
    if fact.source == "cross_person_request":
        return _cross_person_summary(fact, names)
    return fact.source


def _risk_summary(fact: FactEvent) -> str:
    reason = _payload_string(fact.payload, "reason") or "signal-derived risk"
    transition = _payload_string(fact.payload, "transition")
    if transition == "cleared":
        return f"Risk cleared: {reason}"
    return f"Risk opened: {reason}"


def _cross_person_summary(fact: FactEvent, names: Mapping[str, str]) -> str:
    kind = _cross_person_kind_label(fact.payload)
    transition = _payload_string(fact.payload, "transition") or "opened"
    # Names only, never ids, so a fact recorded before the names were captured
    # still reads as a sentence about people. The check-in summary does the same.
    requester = _reporter_name(fact.payload, names) or "someone"
    counterpart = _referenced_person_name(fact.payload, names) or "unresolved counterpart"
    summary = _payload_string_any(fact.payload, ("summary", "note")) or "follow-up needed"
    if transition == "opened":
        return f"Cross-person {kind} opened: {requester} needs {counterpart} for {summary}"
    if transition == "resolved":
        return f"Cross-person {kind} resolved: {counterpart} completed {summary}"
    if transition == "acknowledged":
        return f"Cross-person {kind} acknowledged: {counterpart} is handling {summary}"
    if transition == "needs_resolution":
        return f"Cross-person {kind} needs PM resolution: {requester} named {summary}"
    return f"Cross-person {kind} {transition}: {summary}"


def _reporter_name(payload: Mapping[str, JsonScalar], names: Mapping[str, str]) -> str | None:
    """Who raised a cross-person request; ``None`` when the fact names no one."""
    person_id = _payload_string_any(payload, _REPORTER_ID_KEYS)
    recorded = _payload_string_any(payload, _REPORTER_NAME_KEYS)
    if person_id is None and recorded is None:
        return None
    return person_name(names, person_id, recorded)


def _referenced_person_name(
    payload: Mapping[str, JsonScalar], names: Mapping[str, str]
) -> str | None:
    """Who a cross-person request is for; ``None`` while no counterpart is resolved."""
    person_id = _payload_string_any(payload, _REFERENCED_PERSON_ID_KEYS)
    recorded = _payload_string_any(payload, _REFERENCED_PERSON_NAME_KEYS)
    if person_id is None and recorded is None:
        return None
    return person_name(names, person_id, recorded)


def _details_for_fact(fact: FactEvent, names: Mapping[str, str]) -> Mapping[str, JsonScalar]:
    if fact.source == "checkin":
        return {
            "status_source": _payload_string(fact.payload, "status_source"),
            "blocker_count": _payload_int(fact.payload, "blocker_count"),
            "eta_change_days": _payload_int(fact.payload, "eta_change_days"),
        }
    if fact.source == "work_item":
        return {
            "from_state": _payload_string(fact.payload, "from_state"),
            "to_state": _payload_string(fact.payload, "to_state"),
            "item_type": _payload_string(fact.payload, "item_type"),
            "repo": _payload_string(fact.payload, "repo"),
            "branch": _payload_string(fact.payload, "branch"),
            "pr_id": _payload_string(fact.payload, "pr_id"),
        }
    if fact.source == "vcs_pull_request":
        return {
            "repo": _payload_string(fact.payload, "repo"),
            "id": _payload_string(fact.payload, "id"),
            "merged": _payload_bool(fact.payload, "merged"),
        }
    if fact.source == "vcs_commit":
        return {
            "repo": _payload_string(fact.payload, "repo"),
            "sha": _payload_string(fact.payload, "sha"),
        }
    if fact.source == "issue":
        return {
            "key": _payload_string(fact.payload, "key"),
            "state": _payload_string(fact.payload, "state"),
        }
    if fact.source == "risk":
        return {
            "rule_id": _payload_string(fact.payload, "rule_id"),
            "severity": _payload_string(fact.payload, "severity"),
            "transition": _payload_string(fact.payload, "transition"),
            "evidence_url": _payload_string(fact.payload, "evidence_url"),
            "age_days": _payload_int(fact.payload, "age_days"),
        }
    if fact.source == "cross_person_request":
        return {
            "request_id": _payload_string(fact.payload, "request_id"),
            "reporter_id": _payload_string_any(fact.payload, _REPORTER_ID_KEYS),
            "reporter_name": _reporter_name(fact.payload, names),
            "referenced_person_id": _payload_string_any(fact.payload, _REFERENCED_PERSON_ID_KEYS),
            "referenced_person_name": _referenced_person_name(fact.payload, names),
            "dependency_kind": _payload_string_any(fact.payload, ("dependency_kind", "kind")),
            "dependency_status": _payload_string_any(
                fact.payload,
                ("dependency_status", "status"),
            ),
            "transition": _payload_string(fact.payload, "transition"),
            "summary": _payload_string_any(fact.payload, ("summary", "note")),
            "needs_resolution": _payload_bool(fact.payload, "needs_resolution"),
        }
    return {}


def _cross_person_kind_label(payload: Mapping[str, JsonScalar]) -> str:
    kind = _payload_string_any(payload, ("dependency_kind", "kind")) or "request"
    return {
        "needs_review": "review",
        "needs_input": "input",
        "blocked_by": "blocker",
        "waiting_on": "dependency",
    }.get(kind, kind)


def _payload_string_any(payload: Mapping[str, JsonScalar], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _payload_string(payload, key)
        if value is not None:
            return value
    return None


def _payload_string(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _payload_bool(payload: Mapping[str, JsonScalar], key: str) -> bool | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return None


def _payload_int(payload: Mapping[str, JsonScalar], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
