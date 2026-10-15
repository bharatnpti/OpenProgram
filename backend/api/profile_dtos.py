"""Profiles per pod membership: the catalog, a pod's people, one membership, the review list."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.application.profile_service import (
    MemberProfileView,
    MembershipProfileDetail,
    PodProfilesView,
    ReviewItem,
)
from core.domain.profiles import PodMemberProfile, ProfileDefinition, ProfileSuggestion

#: Where a membership's profile comes from: a row's source, or ``pod_role`` with no row.
ProfileSourceWord = Literal["pod_role", "backfill", "admin", "import", "suggestion_accepted"]


class ProfileResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    label: str
    #: Merge requests are expected from someone with this profile in the pod.
    code_work: bool
    title_words: list[str]
    sort: int
    #: Offered nowhere; memberships already holding it keep it.
    hidden: bool
    #: The tenant changed or added it.
    customized: bool

    @classmethod
    def from_domain(cls, entry: ProfileDefinition) -> ProfileResponse:
        return cls(
            slug=entry.slug,
            label=entry.label,
            code_work=entry.code_work,
            title_words=list(entry.title_words),
            sort=entry.sort,
            hidden=entry.hidden,
            customized=entry.customized,
        )


class ProfileUpdateRequest(BaseModel):
    """A catalog entry as the tenant wants it: a rename, hiding it, or a new one."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=1, max_length=40)
    code_work: bool = False
    hidden: bool = False
    title_words: list[str] = Field(default_factory=list, max_length=12)
    sort: int = Field(default=100, ge=0, le=1000)


class MemberProfileResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    member_id: str
    member_name: str
    role: str | None
    profile: str
    label: str
    code_work: bool
    source: ProfileSourceWord
    #: Someone chose it; false for one from the pod role or the backfill.
    checked: bool
    since: date | None
    set_by_name: str | None
    set_at: datetime | None
    is_lead: bool

    @classmethod
    def from_view(cls, view: MemberProfileView) -> MemberProfileResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            member_id=view.member_id,
            member_name=view.member_name,
            role=view.role,
            profile=view.profile,
            label=view.label,
            code_work=view.code_work,
            source=view.source.value if view.source is not None else "pod_role",
            checked=view.checked,
            since=view.since,
            set_by_name=view.set_by_name,
            set_at=view.set_at,
            is_lead=view.is_lead,
        )


class PodProfilesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    as_of: date
    #: The caller sets profiles in this pod: an admin, or the scrum master running it.
    can_edit: bool
    members: list[MemberProfileResponse]

    @classmethod
    def from_view(cls, view: PodProfilesView, *, can_edit: bool) -> PodProfilesResponse:
        return cls(
            pod_id=view.pod_id,
            pod_name=view.pod_name,
            as_of=view.as_of,
            can_edit=can_edit,
            members=[MemberProfileResponse.from_view(item) for item in view.members],
        )


class ProfileSuggestionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: str
    label: str
    #: Why, in words: "their title says 'QA Engineer'". Never stored.
    reasons: list[str]

    @classmethod
    def from_domain(cls, suggestion: ProfileSuggestion, label: str) -> ProfileSuggestionResponse:
        return cls(profile=suggestion.profile, label=label, reasons=list(suggestion.reasons))


class ProfileHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: str
    valid_from: date
    source: ProfileSourceWord
    set_at: datetime | None

    @classmethod
    def from_domain(cls, row: PodMemberProfile) -> ProfileHistoryEntry:
        return cls(
            profile=row.profile,
            valid_from=row.valid_from,
            source=row.source.value,
            set_at=row.set_at,
        )


class MembershipProfileResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str
    pod_name: str
    member_id: str
    member_name: str
    #: None when the person is not in the pod yet: a suggestion before adding them.
    current: MemberProfileResponse | None
    history: list[ProfileHistoryEntry]
    suggestion: ProfileSuggestionResponse

    @classmethod
    def from_detail(cls, detail: MembershipProfileDetail) -> MembershipProfileResponse:
        return cls(
            pod_id=detail.pod_id,
            pod_name=detail.pod_name,
            member_id=detail.member_id,
            member_name=detail.member_name,
            current=(
                MemberProfileResponse.from_view(detail.current)
                if detail.current is not None
                else None
            ),
            history=[ProfileHistoryEntry.from_domain(row) for row in detail.history],
            suggestion=ProfileSuggestionResponse.from_domain(
                detail.suggestion, detail.suggestion_label
            ),
        )


class SetProfileRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: str = Field(min_length=1, max_length=32)
    #: The person picked the suggestion shown to them.
    accepted_suggestion: bool = False


class ProfileReviewItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: MemberProfileResponse
    suggestion: ProfileSuggestionResponse

    @classmethod
    def from_domain(cls, item: ReviewItem) -> ProfileReviewItemResponse:
        return cls(
            current=MemberProfileResponse.from_view(item.current),
            suggestion=ProfileSuggestionResponse.from_domain(
                item.suggestion, item.suggestion_label
            ),
        )


class ProfileReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Memberships whose profile nobody checked yet, in the pods the caller sets.
    items: list[ProfileReviewItemResponse]


class AcceptProfileItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    profile: str = Field(min_length=1, max_length=32)


class AcceptProfilesRequest(BaseModel):
    """The suggestions the person saw and accepts, exactly as shown."""

    model_config = ConfigDict(frozen=True)

    items: list[AcceptProfileItem] = Field(min_length=1, max_length=500)


class AcceptProfilesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: list[MemberProfileResponse]
