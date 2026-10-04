"""Which synced merge requests name an issue.

A merge request names an issue only by the key in its source branch or title
("CHK-3-payment-intent", "INS-2: backfill"), matched whole: ``INS-2`` is not
``INS-21`` and not ``XINS-2``. The ``merged_issue_open`` drift and the
write-back's open merge request gate both read the links this way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from core.domain.graph import FactEvent, JsonScalar

MERGE_REQUEST_FACT_SOURCE = "vcs_pull_request"
# An issue key as a branch or a merge request title carries it ("CHK-3-payment-intent").
ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_]*-\d+)(?![0-9])")


def merge_requests_by_issue_key(
    facts: Iterable[FactEvent],
    issue_keys: set[str],
    *,
    repo_scope: set[str] | None = None,
) -> dict[str, list[FactEvent]]:
    """The latest fact of each merge request, grouped by the issue key it names.

    ``repo_scope`` limits the requests to those repositories; ``None`` reads
    every repository the facts cover.
    """
    latest: dict[tuple[str, str], FactEvent] = {}
    for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
        repo = _payload_str(fact.payload, "repo")
        pr_id = _payload_str(fact.payload, "id")
        if repo is None or pr_id is None:
            continue
        if repo_scope is None or repo in repo_scope:
            latest[(repo, pr_id)] = fact
    by_key: dict[str, list[FactEvent]] = {}
    upper_keys = {key.upper(): key for key in issue_keys}
    for fact in latest.values():
        text = " ".join(
            value
            for value in (
                _payload_str(fact.payload, "source_branch"),
                _payload_str(fact.payload, "title"),
            )
            if value
        )
        named = {match.upper() for match in ISSUE_KEY.findall(text)}
        for upper in named & set(upper_keys):
            by_key.setdefault(upper_keys[upper], []).append(fact)
    return by_key


def is_open_merge_request(fact: FactEvent) -> bool:
    """Neither merged nor closed. A draft request is open: it is unmerged work."""
    if fact.payload.get("merged") is True:
        return False
    return fact.payload.get("state") not in ("merged", "closed")


def is_merged_merge_request(fact: FactEvent) -> bool:
    """Merged, by the provider-neutral flag or state."""
    return fact.payload.get("merged") is True or fact.payload.get("state") == "merged"


def merge_request_reference(fact: FactEvent) -> str:
    """The request with its full repository path: ``acme/insights-pipeline !2``."""
    repo = _payload_str(fact.payload, "repo") or "repo"
    return f"{repo.rstrip('/')} {_sigil(fact)}{_payload_str(fact.payload, 'id') or '?'}"


def _sigil(fact: FactEvent) -> str:
    web_url = _payload_str(fact.payload, "web_url") or ""
    return "!" if "/merge_requests/" in web_url else "#"


def merge_request_label(fact: FactEvent) -> str:
    """How a person names the request: ``insights-pipeline !1`` (GitLab) or ``api #4``."""
    repo = _payload_str(fact.payload, "repo") or "repo"
    pr_id = _payload_str(fact.payload, "id") or "?"
    web_url = _payload_str(fact.payload, "web_url") or ""
    sigil = "!" if "/merge_requests/" in web_url else "#"
    return f"{repo.rstrip('/').rsplit('/', 1)[-1]} {sigil}{pr_id}"


@dataclass(frozen=True, kw_only=True)
class MergeRequestIndex:
    """The latest fact of each synced merge request, by name and by issue key."""

    by_ref: Mapping[tuple[str, str], FactEvent]
    by_issue: Mapping[str, tuple[FactEvent, ...]]

    @classmethod
    def from_facts(cls, facts: Iterable[FactEvent]) -> MergeRequestIndex:
        latest: dict[tuple[str, str], FactEvent] = {}
        for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
            repo = _payload_str(fact.payload, "repo")
            pr_id = _payload_str(fact.payload, "id")
            if repo is not None and pr_id is not None:
                latest[(_repo_short_name(repo), pr_id)] = fact
        by_issue: dict[str, list[FactEvent]] = {}
        for fact in latest.values():
            text = " ".join(
                value
                for value in (
                    _payload_str(fact.payload, "source_branch"),
                    _payload_str(fact.payload, "title"),
                )
                if value
            )
            for key in {match.upper() for match in ISSUE_KEY.findall(text)}:
                by_issue.setdefault(key, []).append(fact)
        return cls(
            by_ref=latest,
            by_issue={key: tuple(items) for key, items in by_issue.items()},
        )

    def merged_work(
        self,
        *,
        issue_keys: Iterable[str],
        refs: Iterable[tuple[str, str]],
    ) -> tuple[FactEvent, ...] | None:
        """The merge requests an ask is about, when every one of them is merged.

        ``refs`` are ``(repo, number)`` pairs the ask names; ``issue_keys``
        bring in every merge request that names the issue. None when the ask
        names none we know, or when any of them is still open or was closed
        without merging: work that is not in has not happened.
        """
        named: dict[tuple[str, str], FactEvent] = {}
        for repo, number in refs:
            fact = self.by_ref.get((repo.casefold(), number))
            if fact is not None:
                named[(repo.casefold(), number)] = fact
        for key in issue_keys:
            for fact in self.by_issue.get(key.upper(), ()):
                repo = _payload_str(fact.payload, "repo") or ""
                named[(_repo_short_name(repo), _payload_str(fact.payload, "id") or "")] = fact
        if not named or not all(is_merged_merge_request(fact) for fact in named.values()):
            return None
        return tuple(named[key] for key in sorted(named))


def is_merged_merge_request(fact: FactEvent) -> bool:
    return fact.payload.get("merged") is True or fact.payload.get("state") == "merged"


def _repo_short_name(repo: str) -> str:
    return repo.rstrip("/").rsplit("/", 1)[-1].casefold()


def _payload_str(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
