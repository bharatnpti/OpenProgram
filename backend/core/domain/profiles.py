"""A member's profile in a pod: the discipline they bring there (development, QA, product, ...).

A profile is part of a pod membership, so one person can be the QA in one pod
and the scrum master's delivery lead in another. It sits beside the pod role
(developer, scrum master, product owner, manager), which keeps meaning
accountability; the profile defaults from it.

A profile grants no rights. Sign-in roles decide what a person may do; a
profile only routes: who is asked, named and escalated to. It is a role, never
a rating, and is never set on its own: a suggestion (``suggest_profile``) is
shown to whoever sets it, with its reasons, and only they accept it.

Profiles are dated. Each change is a new row from its day, so an earlier day
keeps the profile it had (``current_profile``). The catalog ships with generic
defaults (``default_profiles``); a tenant renames, hides or adds entries as data
(``merged_catalog``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum

from core.domain.errors import OpenProgramError

#: The profile a membership gets when its pod role is none of the known ones.
FALLBACK_PROFILE = "development"
MAX_LABEL = 40
MAX_TITLE_WORDS = 12
MAX_TITLE_WORD = 40
MAX_CATALOG = 30
#: How many days of a person's issues and Git work a suggestion reads.
SUGGESTION_WINDOW_DAYS = 90
#: The share of a person's issues of one kind that suggests its profile.
_ISSUE_SHARE = 0.5
_MIN_ISSUES = 2
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_WORD = re.compile(r"[a-z0-9]+")


class ProfileError(OpenProgramError):
    """A profile or catalog entry that cannot be saved, in plain words."""


class ProfileConflict(ProfileError):
    """A catalog entry whose name another of the tenant's profiles already has."""


def same_name_as(
    label: str, slug: str, catalog: Iterable[ProfileDefinition]
) -> ProfileDefinition | None:
    """Another entry called ``label``, compared without case or extra spaces; None when unique.

    Hidden entries count: a hidden one can be offered again, and two alike in a
    picker could not be told apart.
    """
    folded = " ".join(label.split()).casefold()
    return next(
        (
            item
            for item in catalog
            if item.slug != slug and " ".join(item.label.split()).casefold() == folded
        ),
        None,
    )


class ProfileSource(StrEnum):
    """How a profile row came to be."""

    #: Written by migration 0041 from the pod role; not checked by anyone yet.
    BACKFILL = "backfill"
    #: Set by an admin, or by the scrum master of the pod.
    ADMIN = "admin"
    #: Set while importing people from the directory into a pod.
    IMPORT = "import"
    #: A suggestion someone looked at and accepted.
    SUGGESTION_ACCEPTED = "suggestion_accepted"


#: The sources a person chose: the profile was looked at, so it routes the code-work test.
CHECKED_SOURCES = frozenset(
    {ProfileSource.ADMIN, ProfileSource.IMPORT, ProfileSource.SUGGESTION_ACCEPTED}
)


@dataclass(frozen=True, kw_only=True)
class ProfileDefinition:
    """One entry of the catalog."""

    slug: str
    label: str
    #: Whether merge requests are expected from someone with this profile in the pod.
    code_work: bool
    #: Words of a directory title that suggest it ("qa", "scrum master").
    title_words: tuple[str, ...] = ()
    sort: int = 100
    #: Hidden entries are offered nowhere; a membership already holding one keeps it.
    hidden: bool = False
    #: Jira issue types that suggest it (defaults only; never stored per tenant).
    issue_types: tuple[str, ...] = ()
    #: Words in a person's issue labels that suggest it (defaults only).
    label_words: tuple[str, ...] = ()
    #: True when the tenant changed or added it.
    customized: bool = False


_DEFAULTS: tuple[ProfileDefinition, ...] = (
    ProfileDefinition(
        slug="development",
        label="Development",
        code_work=True,
        title_words=("engineer", "developer", "programmer", "software"),
        sort=10,
        issue_types=("story", "task", "sub-task", "subtask"),
    ),
    ProfileDefinition(
        slug="qa",
        label="QA and test",
        code_work=False,
        title_words=("qa", "test", "tester", "testing", "quality"),
        sort=20,
        issue_types=("bug", "test", "test execution", "test plan", "test case"),
    ),
    ProfileDefinition(
        slug="security",
        label="Security",
        code_work=False,
        title_words=("security", "appsec", "infosec"),
        sort=30,
        label_words=("security", "vulnerability", "appsec"),
    ),
    ProfileDefinition(
        slug="product",
        label="Product",
        code_work=False,
        title_words=("product", "product owner", "product manager", "business analyst", "po"),
        sort=40,
        issue_types=("epic",),
    ),
    ProfileDefinition(
        slug="design",
        label="UX and design",
        code_work=False,
        title_words=("design", "designer", "ux", "ui"),
        sort=50,
        label_words=("design", "ux"),
    ),
    ProfileDefinition(
        slug="delivery",
        label="TPM / delivery",
        code_work=False,
        title_words=(
            "tpm",
            "program manager",
            "programme manager",
            "project manager",
            "scrum master",
            "delivery",
            "agile coach",
        ),
        sort=60,
    ),
    ProfileDefinition(
        slug="ops",
        label="Ops / SRE / platform",
        code_work=True,
        title_words=("sre", "devops", "platform", "ops", "operations", "reliability"),
        sort=70,
        issue_types=("incident", "change", "problem"),
    ),
    ProfileDefinition(
        slug="data",
        label="Data and analytics",
        code_work=True,
        title_words=("data", "analyst", "analytics", "ml", "machine learning"),
        sort=80,
    ),
    ProfileDefinition(
        slug="architecture",
        label="Architecture",
        code_work=False,
        title_words=("architect", "architecture"),
        sort=90,
    ),
    ProfileDefinition(
        slug="management",
        label="Engineering management",
        code_work=False,
        title_words=("manager", "head of", "director", "vp"),
        sort=100,
    ),
    ProfileDefinition(slug="other", label="Other", code_work=False, sort=110),
)

#: The profile each known pod role defaults to (the backfill's and a new membership's).
POD_ROLE_PROFILES: Mapping[str, str] = {
    "developer": "development",
    "product_owner": "product",
    "scrum_master": "delivery",
    "manager": "management",
}


def default_profiles() -> tuple[ProfileDefinition, ...]:
    """The generic catalog every tenant starts with."""
    return _DEFAULTS


def profile_for_pod_role(role: str | None) -> str:
    """The profile a pod role stands for: developer is development, and so on."""
    key = (role or "").strip().lower().replace(" ", "_")
    return POD_ROLE_PROFILES.get(key, FALLBACK_PROFILE)


def merged_catalog(overrides: Iterable[ProfileDefinition]) -> tuple[ProfileDefinition, ...]:
    """The defaults with a tenant's changes over them, in their sort order.

    An override of a shipped slug keeps the shipped issue types and label words
    (suggestion inputs that are not stored per tenant); a new slug is added.
    """
    by_slug = {item.slug: item for item in _DEFAULTS}
    for override in overrides:
        shipped = by_slug.get(override.slug)
        by_slug[override.slug] = replace(
            override,
            issue_types=shipped.issue_types if shipped else override.issue_types,
            label_words=shipped.label_words if shipped else override.label_words,
            customized=True,
        )
    return tuple(sorted(by_slug.values(), key=lambda item: (item.sort, item.label.casefold())))


def catalog_by_slug(catalog: Iterable[ProfileDefinition]) -> dict[str, ProfileDefinition]:
    return {item.slug: item for item in catalog}


def profile_label(slug: str, catalog: Iterable[ProfileDefinition]) -> str:
    """The catalog's label, or the slug in words for one the catalog no longer lists."""
    known = catalog_by_slug(catalog).get(slug)
    if known is not None:
        return known.label
    words = slug.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def validated_entry(entry: ProfileDefinition) -> ProfileDefinition:
    """A catalog entry tidied, or ProfileError naming what is wrong."""
    problems: list[str] = []
    if not _SLUG.match(entry.slug):
        problems.append(
            "a profile's id is 1 to 32 lower-case letters, digits or underscores, "
            "starting with a letter"
        )
    label = " ".join(entry.label.split())
    if not label or len(label) > MAX_LABEL:
        problems.append(f"a profile needs a name of 1 to {MAX_LABEL} characters")
    words = tuple(
        dict.fromkeys(" ".join(word.lower().split()) for word in entry.title_words if word.strip())
    )
    if len(words) > MAX_TITLE_WORDS or any(len(word) > MAX_TITLE_WORD for word in words):
        problems.append(
            f"name at most {MAX_TITLE_WORDS} title words, each at most {MAX_TITLE_WORD} characters"
        )
    if problems:
        raise ProfileError(_sentence(problems))
    return replace(entry, label=label, title_words=words)


def ensure_offered(slug: str, catalog: Iterable[ProfileDefinition]) -> ProfileDefinition:
    """The catalog entry a person may pick, or ProfileError when it is unknown or hidden."""
    known = catalog_by_slug(catalog).get(slug.strip())
    if known is None:
        raise ProfileError(f"There is no profile {slug.strip()!r}. Pick one from the list.")
    if known.hidden:
        raise ProfileError(f"The profile {known.label!r} is hidden. Pick another one.")
    return known


# ---- a membership's profile, dated --------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PodMemberProfile:
    """One row: the member's profile in the pod from ``valid_from`` on."""

    tenant_id: str
    pod_id: str
    member_id: str
    profile: str
    valid_from: date
    source: ProfileSource
    #: The one to go to first when several in the pod hold the profile.
    is_lead: bool = False
    set_by: str | None = None
    set_at: datetime | None = None

    @property
    def checked(self) -> bool:
        """Someone chose it; a backfilled row only mirrors the pod role."""
        return self.source in CHECKED_SOURCES


def current_profile(rows: Iterable[PodMemberProfile], on: date) -> PodMemberProfile | None:
    """The membership's profile on ``on``: the latest row starting on or before it."""
    held = [row for row in rows if row.valid_from <= on]
    if not held:
        return None
    return max(held, key=lambda row: (row.valid_from, row.set_at or _EPOCH))


def current_profiles(
    rows: Iterable[PodMemberProfile], on: date
) -> dict[tuple[str, str], PodMemberProfile]:
    """Each (pod, member)'s profile on ``on``."""
    grouped: dict[tuple[str, str], list[PodMemberProfile]] = {}
    for row in rows:
        grouped.setdefault((row.pod_id, row.member_id), []).append(row)
    found: dict[tuple[str, str], PodMemberProfile] = {}
    for key, items in grouped.items():
        current = current_profile(items, on)
        if current is not None:
            found[key] = current
    return found


# ---- suggestion -----------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ProfileSuggestion:
    """A profile to offer, and why, in words. Shown to whoever sets it; never stored."""

    profile: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class SuggestionInputs:
    """What a suggestion reads about one person in one pod."""

    title: str | None = None
    #: The Jira issue types of their issues in the pod's remit, one per issue.
    issue_types: Sequence[str] = ()
    #: The labels and components on those issues, one entry per issue.
    issue_labels: Sequence[Sequence[str]] = ()
    #: Commits and merge requests of theirs in the window.
    git_events: int = 0
    pod_role: str | None = None


def suggest_profile(
    catalog: Sequence[ProfileDefinition], inputs: SuggestionInputs
) -> ProfileSuggestion:
    """The profile the person's title, issues and Git work point to, with the reasons.

    1. Their directory title, against each profile's title words: the longest
       phrase found wins, then the one found first ("QA Engineer" is QA, not
       development).
    2. Else their issues: half or more of at least two of one kind of issue
       (Bug and Test for QA, Incident and Change for ops), or labels naming a
       profile (security, design).
    3. Else any commit or merge request of theirs: development.
    4. Else the profile their pod role stands for.

    Evidence that agrees with the pick is added to its reasons. Hidden entries
    are never suggested.
    """
    offered = [item for item in catalog if not item.hidden]
    by_slug = catalog_by_slug(offered)
    titled = _title_match(inputs.title, offered)
    from_issues = _issue_match(inputs, offered)
    git = (
        f"{_count(inputs.git_events, 'commit or merge request', 'commits or merge requests')} "
        f"of theirs in the last {SUGGESTION_WINDOW_DAYS} days"
        if inputs.git_events > 0
        else None
    )
    if titled is not None:
        slug, reasons = titled[0], [titled[1]]
    elif from_issues is not None:
        slug, reasons = from_issues[0], [from_issues[1]]
    elif git is not None and FALLBACK_PROFILE in by_slug:
        slug, reasons = FALLBACK_PROFILE, [git]
    else:
        slug = profile_for_pod_role(inputs.pod_role)
        if slug not in by_slug:
            slug = offered[0].slug if offered else FALLBACK_PROFILE
        role = (inputs.pod_role or "").replace("_", " ").strip()
        reasons = [f"from their role in the pod ({role})" if role else "from their role in the pod"]
    if from_issues is not None and from_issues[0] == slug and from_issues[1] not in reasons:
        reasons.append(from_issues[1])
    chosen = by_slug.get(slug)
    if git is not None and git not in reasons and chosen is not None and chosen.code_work:
        reasons.append(git)
    return ProfileSuggestion(profile=slug, reasons=tuple(reasons))


def title_words_in(title: str) -> list[str]:
    return _WORD.findall(title.lower())


def _title_match(title: str | None, catalog: Sequence[ProfileDefinition]) -> tuple[str, str] | None:
    if not title or not title.strip():
        return None
    words = title_words_in(title)
    best: tuple[int, int, int, str] | None = None
    for index, item in enumerate(catalog):
        for phrase in item.title_words:
            needle = title_words_in(phrase)
            if not needle:
                continue
            at = _find(words, needle)
            if at is None:
                continue
            rank = (-len(needle), at, index, item.slug)
            if best is None or rank < best:
                best = rank
    if best is None:
        return None
    return best[3], f"their title says '{' '.join(title.split())}'"


def _find(words: Sequence[str], needle: Sequence[str]) -> int | None:
    size = len(needle)
    for start in range(len(words) - size + 1):
        if list(words[start : start + size]) == list(needle):
            return start
    return None


def _issue_match(
    inputs: SuggestionInputs, catalog: Sequence[ProfileDefinition]
) -> tuple[str, str] | None:
    types = [" ".join(kind.lower().split()) for kind in inputs.issue_types if kind.strip()]
    total = len(types)
    if total >= _MIN_ISSUES:
        best: tuple[int, int, str, str] | None = None
        for index, item in enumerate(catalog):
            if not item.issue_types or item.slug == FALLBACK_PROFILE:
                continue
            matched = [kind for kind in types if kind in item.issue_types]
            if matched and len(matched) / total >= _ISSUE_SHARE:
                named = " and ".join(sorted({kind.title() for kind in matched}))
                reason = f"{len(matched)} of their {total} issues in this pod are {named}"
                rank = (-len(matched), index, item.slug, reason)
                if best is None or rank < best:
                    best = rank
        if best is not None:
            return best[2], best[3]
    for item in catalog:
        if not item.label_words:
            continue
        hits = sum(
            1
            for labels in inputs.issue_labels
            if any(word in label.lower() for label in labels for word in item.label_words)
        )
        if hits:
            return (
                item.slug,
                f"{_count(hits, 'issue', 'issues')} of theirs carry a "
                f"'{item.label_words[0]}' label or component",
            )
    return None


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sentence(problems: Sequence[str]) -> str:
    text = problems[0] if len(problems) == 1 else "; ".join(problems)
    return text[:1].upper() + text[1:] + "."
