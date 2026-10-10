"""Gates and questions: set up, suggested from Jira, confirmed, signed off, and read.

A scan reads each requirement's Jira text (only when the issue changed since
its last read), suggests the gate items and questions it finds, and leaves
alone everything a person already decided: a dismissed suggestion is not
suggested again, and a question status a person set is not overwritten.

Signing an item off is limited by its kind: only the roles a template names
for that kind may mark it met, failed or waived, and a kind that needs
evidence is met only with a link to it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import uuid4

from core.application.delivery_service import DeliveryService
from core.application.gate_extraction import ModelItemFinder, extract_from_issue
from core.domain.auth import Role
from core.domain.delivery import DeliveryStage, StageMapping, place
from core.domain.errors import AuthorizationDenied, GraphNotFound, ProviderUnavailable
from core.domain.forecast import Release
from core.domain.gates import (
    SIGN_OFF_STATUSES,
    ExtractedItem,
    ExtractedQuestion,
    GateError,
    GateEvaluation,
    GateItem,
    GateState,
    GateTemplate,
    ItemSource,
    ItemStatus,
    QuestionStatus,
    TrackedQuestion,
    default_templates,
    evaluate_gate,
    item_fingerprint,
    past_its_gate,
    validated_item_text,
    validated_template,
)
from core.domain.graph import GraphNode, NodeKind
from core.ports.gates import (
    GateItemRepository,
    GateTemplateRepository,
    IssueScanRepository,
    QuestionRepository,
)
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import GraphRepository

MAX_SCAN = 200
SCAN_ACTOR = "scan"

ROLE_NAMES = {
    Role.DEV: "developer",
    Role.SM: "scrum master",
    Role.PO: "product owner",
    Role.MGR: "manager",
    Role.EXEC: "executive",
    Role.ADMIN: "admin",
}


def _article(word: str) -> str:
    return "an" if word[:1].casefold() in "aeiou" else "a"


def _either(names: Iterable[str]) -> str:
    """'product owner or manager'."""
    kept = list(names)
    return kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + " or " + kept[-1]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class IssueGatesView:
    key: str
    title: str
    stage: DeliveryStage
    status: str | None
    evaluations: tuple[GateEvaluation, ...]
    items: tuple[GateItem, ...]
    #: Gates whose stage the issue reached without passing them.
    passed_without: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class GateBoardView:
    project_id: str
    release_id: str | None
    templates: tuple[GateTemplate, ...]
    issues: tuple[IssueGatesView, ...]
    questions: tuple[TrackedQuestion, ...]
    #: Display names of the people who signed off items or asked questions, by id.
    actor_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ScanSummary:
    read: int
    unchanged: int
    failed: int
    suggested_items: int
    questions: int


class GateService:
    def __init__(
        self,
        *,
        template_repository: GateTemplateRepository,
        item_repository: GateItemRepository,
        question_repository: QuestionRepository,
        scan_repository: IssueScanRepository,
        delivery_service: DeliveryService,
        issue_tracker: IssueTracker,
        model_finder: ModelItemFinder | None = None,
        graph_repository: GraphRepository | None = None,
        clock: Callable[[], datetime] = _utc_now,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._templates = template_repository
        self._items = item_repository
        self._questions = question_repository
        self._scans = scan_repository
        self._delivery = delivery_service
        self._tracker = issue_tracker
        self._finder = model_finder
        self._graph = graph_repository
        self._clock = clock
        self._new_id = new_id

    # ---- templates -----------------------------------------------------------

    async def templates(self, tenant_id: str) -> tuple[list[GateTemplate], bool]:
        """The tenant's templates, and whether they are still the defaults."""
        stored = await self._templates.list(tenant_id)
        if stored:
            return sorted(stored, key=lambda item: item.name.casefold()), False
        return list(default_templates(tenant_id)), True

    async def save_template(self, template: GateTemplate, *, actor: str) -> GateTemplate:
        stored, is_default = await self.templates(template.tenant_id)
        if is_default:
            # The first change makes the defaults the tenant's own, so the
            # others do not vanish when one is edited.
            for default in stored:
                if default.template_id != template.template_id:
                    await self._templates.save(
                        replace(default, updated_at=self._clock(), updated_by=actor)
                    )
        checked = validated_template(
            replace(
                template,
                template_id=template.template_id or self._new_id(),
                updated_at=self._clock(),
                updated_by=actor,
            )
        )
        await self._templates.save(checked)
        return checked

    async def delete_template(self, tenant_id: str, template_id: str, *, actor: str) -> None:
        stored, is_default = await self.templates(tenant_id)
        if is_default:
            for default in stored:
                if default.template_id != template_id:
                    await self._templates.save(
                        replace(default, updated_at=self._clock(), updated_by=actor)
                    )
            return
        await self._templates.delete(tenant_id, template_id)

    # ---- the board ------------------------------------------------------------

    async def board(
        self, tenant_id: str, project_id: str, as_of: date, release: Release | None = None
    ) -> GateBoardView:
        if not await self._delivery.is_project(tenant_id, project_id, as_of):
            raise GraphNotFound(f"No project {project_id!r}.")
        templates = [item for item in (await self.templates(tenant_id))[0] if item.enabled]
        tasks = await self._delivery.scope_tasks(tenant_id, project_id, as_of, release)
        mapping = (await self._delivery.delivery_settings(tenant_id)).settings.mapping
        keys = [_key(task) for task in tasks]
        items = await self._items.list_for_issues(tenant_id, keys)
        by_issue: dict[str, list[GateItem]] = {}
        for item in items:
            if item.status is not ItemStatus.DISMISSED:
                by_issue.setdefault(item.issue_key, []).append(item)
        issues = [
            _issue_view(task, templates, by_issue.get(_key(task), []), mapping) for task in tasks
        ]
        questions = [
            question
            for question in await self._questions.list_for_issues(tenant_id, keys)
            if not question.dismissed
        ]
        actors = {
            actor
            for issue in issues
            for item in issue.items
            for actor in (item.signed_by, item.created_by)
            if actor
        } | {question.asked_by for question in questions if not question.asked_by_name}
        return GateBoardView(
            project_id=project_id,
            release_id=release.release_id if release is not None else None,
            templates=tuple(templates),
            issues=tuple(sorted(issues, key=lambda issue: issue.key)),
            questions=tuple(questions),
            actor_names=await self._names(tenant_id, actors),
        )

    async def _names(self, tenant_id: str, actors: set[str]) -> dict[str, str]:
        if self._graph is None or not actors:
            return {}
        return {
            node.id: node.name
            for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER)
            if node.id in actors
        }

    # ---- items -----------------------------------------------------------------

    async def add_item(
        self,
        tenant_id: str,
        issue_key: str,
        *,
        template_id: str,
        kind: str,
        text: str,
        actor: str,
    ) -> GateItem:
        template = await self._template(tenant_id, template_id)
        if template.kind(kind) is None:
            raise GateError(f"{template.name} has no kind {kind!r}.")
        now = self._clock()
        item = GateItem(
            tenant_id=tenant_id,
            item_id=self._new_id(),
            issue_key=issue_key,
            template_id=template_id,
            kind=kind,
            text=validated_item_text(text),
            status=ItemStatus.PENDING,
            source=ItemSource.MANUAL,
            source_ref="",
            created_at=now,
            updated_at=now,
            created_by=actor,
        )
        await self._items.save(item)
        return item

    async def confirm_item(self, tenant_id: str, item_id: str, *, actor: str) -> GateItem:
        item = await self._item(tenant_id, item_id)
        if item.status not in {ItemStatus.SUGGESTED, ItemStatus.DISMISSED}:
            return item
        updated = replace(item, status=ItemStatus.PENDING, updated_at=self._clock(), note=item.note)
        await self._items.save(updated)
        return updated

    async def dismiss_item(self, tenant_id: str, item_id: str, *, actor: str) -> GateItem:
        item = await self._item(tenant_id, item_id)
        updated = replace(item, status=ItemStatus.DISMISSED, updated_at=self._clock())
        await self._items.save(updated)
        return updated

    async def sign_off(
        self,
        tenant_id: str,
        item_id: str,
        status: ItemStatus,
        *,
        actor: str,
        roles: frozenset[Role],
        evidence_url: str | None = None,
        note: str = "",
    ) -> GateItem:
        item = await self._item(tenant_id, item_id)
        template = await self._template(tenant_id, item.template_id)
        kind = template.kind(item.kind)
        if kind is None:
            raise GateError("That item's kind is no longer part of its gate.")
        if status is ItemStatus.PENDING:
            updated = replace(
                item, status=status, signed_by=None, signed_at=None, updated_at=self._clock()
            )
            await self._items.save(updated)
            return updated
        if status not in SIGN_OFF_STATUSES:
            raise GateError("An item is signed off as met, failed or waived.")
        if Role.ADMIN not in roles and not roles & set(kind.sign_off_roles):
            allowed = _either(ROLE_NAMES[role] for role in kind.sign_off_roles)
            raise AuthorizationDenied(
                f"Only {_article(allowed)} {allowed} signs off {_article(kind.label)} "
                f"{kind.label.lower()}."
            )
        evidence = (evidence_url or "").strip() or item.evidence_url
        if status is ItemStatus.MET and kind.evidence_required and not evidence:
            raise GateError(
                f"{_article(kind.label).capitalize()} {kind.label.lower()} is met only with a "
                "link to its evidence."
            )
        if evidence and not evidence.startswith(("https://", "http://")):
            raise GateError("Evidence is a link starting with https://.")
        now = self._clock()
        updated = replace(
            item,
            status=status,
            signed_by=actor,
            signed_at=now,
            evidence_url=evidence,
            note=" ".join(note.split())[:300] or item.note,
            updated_at=now,
        )
        await self._items.save(updated)
        return updated

    # ---- questions --------------------------------------------------------------

    async def update_question(
        self,
        tenant_id: str,
        question_id: str,
        *,
        actor: str,
        confirmed: bool | None = None,
        dismissed: bool | None = None,
        status: QuestionStatus | None = None,
    ) -> TrackedQuestion:
        question = await self._questions.get(tenant_id, question_id)
        if question is None:
            raise GraphNotFound(f"No question {question_id!r}.")
        updated = replace(
            question,
            confirmed=question.confirmed if confirmed is None else confirmed,
            dismissed=question.dismissed if dismissed is None else dismissed,
            status=status or question.status,
            status_set_by_person=question.status_set_by_person or status is not None,
            updated_at=self._clock(),
            updated_by=actor,
        )
        await self._questions.save(updated)
        return updated

    async def add_question(
        self,
        tenant_id: str,
        issue_key: str,
        *,
        asked_to: str,
        summary: str,
        actor: str,
    ) -> TrackedQuestion:
        now = self._clock()
        question = TrackedQuestion(
            tenant_id=tenant_id,
            question_id=self._new_id(),
            issue_key=issue_key,
            comment_ref="",
            asked_by=actor,
            asked_to=" ".join(asked_to.split()),
            asked_to_name=" ".join(asked_to.split()),
            asked_at=now,
            summary=validated_item_text(summary),
            status=QuestionStatus.NOT_YET,
            confirmed=True,
            updated_at=now,
            updated_by=actor,
        )
        await self._questions.save(question)
        return question

    # ---- scanning ------------------------------------------------------------------

    async def scan_scope(
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
        release: Release | None = None,
        *,
        force: bool = False,
    ) -> ScanSummary:
        tasks = await self._delivery.scope_tasks(tenant_id, project_id, as_of, release)
        return await self._scan(tenant_id, tasks[:MAX_SCAN], force=force)

    async def scan_tasks(self, tenant_id: str, tasks: Sequence[GraphNode]) -> ScanSummary:
        return await self._scan(tenant_id, tasks, force=False)

    async def _scan(
        self, tenant_id: str, tasks: Sequence[GraphNode], *, force: bool
    ) -> ScanSummary:
        templates = [item for item in (await self.templates(tenant_id))[0] if item.enabled]
        read = unchanged = failed = suggested = questions = 0
        for task in tasks:
            key = _key(task)
            changed = _iso(task.metadata.get("updated_at"))
            last = await self._scans.last_scanned(tenant_id, key)
            if not force and last is not None and changed is not None and changed <= last:
                unchanged += 1
                continue
            try:
                text = await self._tracker.get_issue_text(tenant_id, key)
            except ProviderUnavailable:
                failed += 1
                continue
            read += 1
            applicable = [
                template
                for template in templates
                if template.applies_to(_text(task.metadata.get("issue_type")))
            ]
            found = extract_from_issue(text, applicable)
            extracted = list(found.items)
            if self._finder is not None:
                extracted += await self._finder.find(
                    tenant_id, text, applicable, already=found.items
                )
            suggested += await self._suggest_items(tenant_id, key, extracted)
            questions += await self._record_questions(tenant_id, key, found.questions)
            await self._scans.record(tenant_id, key, text.updated_at or changed, self._clock())
        return ScanSummary(
            read=read,
            unchanged=unchanged,
            failed=failed,
            suggested_items=suggested,
            questions=questions,
        )

    async def _suggest_items(
        self, tenant_id: str, issue_key: str, extracted: Sequence[ExtractedItem]
    ) -> int:
        known = {
            item.fingerprint for item in await self._items.list_for_issues(tenant_id, [issue_key])
        }
        added = 0
        for found in extracted:
            fingerprint = item_fingerprint(issue_key, found.template_id, found.kind, found.text)
            if fingerprint in known:
                continue
            known.add(fingerprint)
            now = self._clock()
            await self._items.save(
                GateItem(
                    tenant_id=tenant_id,
                    item_id=self._new_id(),
                    issue_key=issue_key,
                    template_id=found.template_id,
                    kind=found.kind,
                    text=found.text,
                    status=ItemStatus.SUGGESTED,
                    source=found.source,
                    source_ref=found.source_ref,
                    created_at=now,
                    updated_at=now,
                    created_by=SCAN_ACTOR,
                )
            )
            added += 1
        return added

    async def _record_questions(
        self, tenant_id: str, issue_key: str, found: Sequence[ExtractedQuestion]
    ) -> int:
        existing = {
            question.comment_ref: question
            for question in await self._questions.list_for_issues(tenant_id, [issue_key])
            if question.comment_ref
        }
        count = 0
        for item in found:
            count += 1
            current = existing.get(item.comment_ref)
            if current is not None:
                if current.status_set_by_person or current.status == item.status:
                    continue
                await self._questions.save(
                    replace(
                        current,
                        status=item.status,
                        answered_ref=item.answered_ref,
                        updated_at=self._clock(),
                        updated_by=SCAN_ACTOR,
                    )
                )
                continue
            await self._questions.save(
                TrackedQuestion(
                    tenant_id=tenant_id,
                    question_id=self._new_id(),
                    issue_key=issue_key,
                    comment_ref=item.comment_ref,
                    asked_by=item.asked_by,
                    asked_by_name=item.asked_by_name,
                    asked_to=item.asked_to,
                    asked_to_name=item.asked_to_name,
                    asked_at=item.asked_at,
                    summary=item.summary,
                    status=item.status,
                    answered_ref=item.answered_ref,
                    confirmed=False,
                    updated_at=self._clock(),
                    updated_by=SCAN_ACTOR,
                )
            )
        return count

    async def _item(self, tenant_id: str, item_id: str) -> GateItem:
        item = await self._items.get(tenant_id, item_id)
        if item is None:
            raise GraphNotFound(f"No item {item_id!r}.")
        return item

    async def _template(self, tenant_id: str, template_id: str) -> GateTemplate:
        templates, _default = await self.templates(tenant_id)
        template = next((item for item in templates if item.template_id == template_id), None)
        if template is None:
            raise GraphNotFound(f"No gate {template_id!r}.")
        return template


def _issue_view(
    task: GraphNode,
    templates: Sequence[GateTemplate],
    items: Sequence[GateItem],
    mapping: StageMapping,
) -> IssueGatesView:
    status = _text(task.metadata.get("status"))
    placement = place(mapping, status=status, state=_text(task.metadata.get("state")))
    stage = placement.stage or DeliveryStage.RAISED
    issue_type = _text(task.metadata.get("issue_type"))
    applicable = [template for template in templates if template.applies_to(issue_type)]
    evaluations = tuple(evaluate_gate(template, items) for template in applicable)
    passed_without = tuple(
        evaluation.template.name
        for evaluation in evaluations
        if past_its_gate(stage, evaluation.template) and evaluation.state is not GateState.PASSED
    )
    return IssueGatesView(
        key=_key(task),
        title=task.name,
        stage=stage,
        status=status,
        evaluations=evaluations,
        items=tuple(items),
        passed_without=passed_without,
    )


def gate_counts(board: GateBoardView) -> Mapping[str, Mapping[GateState, int]]:
    """Per gate, how many issues are passed, open, failed or missing items."""
    counts: dict[str, dict[GateState, int]] = {
        template.template_id: {state: 0 for state in GateState} for template in board.templates
    }
    for issue in board.issues:
        for evaluation in issue.evaluations:
            counts[evaluation.template.template_id][evaluation.state] += 1
    return counts


def _key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
