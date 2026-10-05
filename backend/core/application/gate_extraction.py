"""Find gate items and questions in a Jira issue's description and comments.

The deterministic reader comes first and handles how teams usually write:

- a heading a gate kind names ("Acceptance criteria", "h3. Test cases",
  "**AC:**"), followed by a list, a checklist or a short paragraph; or
  the heading with its item on the same line ("AC: refunds post within a day");
- Given/When/Then scenarios, for kinds that read them;
- a comment that mentions someone and asks a question.

Where a description carries text but no heading, a language model may be
asked to point at the criteria in it. Its answer is used only where each item
it returns appears, word for word, in the text: a model can find what is
written, never add to it. Everything found is a suggestion until a person
confirms it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.application.json_parsing import extract_json_object
from core.domain.gates import (
    MAX_ITEM_TEXT,
    ExtractedItem,
    ExtractedQuestion,
    Extraction,
    GateTemplate,
    ItemKind,
    ItemSource,
    QuestionStatus,
    item_fingerprint,
)
from core.domain.integrations import IssueComment, IssueState, IssueText
from core.domain.llm import LlmRequest
from core.ports.llm import LlmProvider

MAX_ITEMS_PER_ISSUE = 50
MAX_SUMMARY = 240
_MIN_TEXT_FOR_MODEL = 60
_MAX_TEXT_FOR_MODEL = 8000

_LIST_MARKER = re.compile(r"^\s*(?:[-*•+]|#+(?=\s)|\d+[.)]|\[[ xX]\])\s+")
_CHECKBOX = re.compile(r"^\s*\[[ xX]\]\s+")
_HEADING_MARKUP = re.compile(r"^\s*(?:#{1,6}\s*|h[1-6]\.\s*)")
_EMPHASIS = re.compile(r"^[*_]+|[*_]+$")
_GHERKIN_SCENARIO = re.compile(r"^\s*(?:scenario(?: outline)?|szenario)\s*:\s*(.*)$", re.I)
_GHERKIN_STEP = re.compile(r"^\s*(given|when|then|and|but|gegeben|wenn|dann|und)\b", re.I)
_QUESTION_LEAD = re.compile(r"^\s*(?:q|question|frage)\s*[:.-]\s*", re.I)
_SENTENCE = re.compile(r"[^.?!]*\?")


@dataclass(frozen=True, kw_only=True)
class _Block:
    text: str
    source: ItemSource
    ref: str


def extract_from_issue(issue: IssueText, templates: Sequence[GateTemplate]) -> Extraction:
    """Items and questions written in the issue, before any model is asked."""
    blocks = [_Block(text=issue.description, source=ItemSource.DESCRIPTION, ref="description")]
    blocks += [
        _Block(text=comment.body, source=ItemSource.COMMENT, ref=comment.id)
        for comment in issue.comments
    ]
    items: list[ExtractedItem] = []
    seen: set[str] = set()
    for block in blocks:
        for template in templates:
            for kind in template.kinds:
                for text in _items_in(block.text, kind):
                    fingerprint = item_fingerprint(issue.key, template.template_id, kind.key, text)
                    if fingerprint in seen or len(items) >= MAX_ITEMS_PER_ISSUE:
                        continue
                    seen.add(fingerprint)
                    items.append(
                        ExtractedItem(
                            template_id=template.template_id,
                            kind=kind.key,
                            text=text,
                            source=block.source,
                            source_ref=block.ref,
                        )
                    )
    return Extraction(items=tuple(items), questions=tuple(questions_in(issue)))


def questions_in(issue: IssueText) -> list[ExtractedQuestion]:
    """Questions asked in comments, with whether the person asked has replied since."""
    questions: list[ExtractedQuestion] = []
    comments = sorted(issue.comments, key=lambda item: (item.created_at is None, item.created_at))
    for index, comment in enumerate(comments):
        summary = _question_summary(comment)
        if summary is None or comment.author is None or comment.created_at is None:
            continue
        asked_to = next(
            (
                person
                for person in comment.mentions
                if person.external_id != comment.author.external_id
            ),
            None,
        )
        if asked_to is None and not _QUESTION_LEAD.match(comment.body):
            continue
        reply = (
            next(
                (
                    later
                    for later in comments[index + 1 :]
                    if later.author is not None and later.author.external_id == asked_to.external_id
                ),
                None,
            )
            if asked_to is not None
            else None
        )
        if reply is not None:
            status = QuestionStatus.ANSWERED
        elif issue.state is IssueState.DONE:
            status = QuestionStatus.CLOSED_UNANSWERED
        else:
            status = QuestionStatus.NOT_YET
        questions.append(
            ExtractedQuestion(
                comment_ref=comment.id,
                asked_by=comment.author.external_id,
                asked_to=asked_to.external_id if asked_to is not None else "",
                asked_at=comment.created_at,
                summary=summary,
                status=status,
                answered_ref=reply.id if reply is not None else None,
                asked_by_name=comment.author.display_name or "",
                asked_to_name=(asked_to.display_name or "") if asked_to is not None else "",
            )
        )
    return questions


class ModelItemFinder:
    """Ask a model to point at items in text that has no headings, and keep only real quotes."""

    def __init__(self, llm: LlmProvider, *, model: str) -> None:
        self._llm = llm
        self._model = model

    async def find(
        self,
        tenant_id: str,
        issue: IssueText,
        templates: Sequence[GateTemplate],
        *,
        already: Iterable[ExtractedItem],
    ) -> list[ExtractedItem]:
        found_kinds = {(item.template_id, item.kind) for item in already}
        wanted = [
            (template, kind)
            for template in templates
            for kind in template.kinds
            if (template.template_id, kind.key) not in found_kinds
        ]
        text = issue.description.strip()
        if not wanted or len(text) < _MIN_TEXT_FOR_MODEL:
            return []
        kinds = {kind.key: (template, kind) for template, kind in wanted}
        prompt = (
            "The text below is a work item's description. Quote, exactly as written, each "
            "statement in it that is one of these kinds: "
            + "; ".join(f"{key} ({kind.label})" for key, (_template, kind) in kinds.items())
            + '. Answer with JSON only: {"items": [{"kind": "<kind>", "quote": "<exact words>"}]}. '
            "Quote nothing that is not in the text, and return an empty list when there is none."
            f"\n\n---\n{text[:_MAX_TEXT_FOR_MODEL]}\n---"
        )
        try:
            response = await self._llm.complete(
                LlmRequest(
                    tenant_id=tenant_id,
                    prompt=prompt,
                    model=self._model,
                    correlation_id=f"gate-items:{issue.key}",
                    json_mode=True,
                    metadata={"purpose": "gate_item_extraction", "issue_key": issue.key},
                )
            )
        except Exception:  # noqa: BLE001 - a model that fails finds nothing
            return []
        payload = extract_json_object(response.text) or {}
        raw = payload.get("items")
        results: list[ExtractedItem] = []
        source = _normal(text)
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            key = entry.get("kind")
            quote = entry.get("quote")
            if not isinstance(key, str) or not isinstance(quote, str) or key not in kinds:
                continue
            clean = " ".join(quote.split())[:MAX_ITEM_TEXT]
            # Grounding: the quote must be in the text, so nothing is invented.
            if len(clean) < 8 or _normal(clean) not in source:
                continue
            template, _kind = kinds[key]
            results.append(
                ExtractedItem(
                    template_id=template.template_id,
                    kind=key,
                    text=clean,
                    source=ItemSource.DESCRIPTION,
                    source_ref="description",
                )
            )
        return results[:MAX_ITEMS_PER_ISSUE]


def _items_in(text: str, kind: ItemKind) -> list[str]:
    lines = text.splitlines()
    items: list[str] = []
    index = 0
    while index < len(lines):
        heading, inline = _heading_match(lines[index], kind.headings)
        if heading:
            if inline:
                items.append(inline)
            index, found = _section_items(lines, index + 1, kind.headings)
            items.extend(found)
            continue
        index += 1
    if kind.gherkin:
        items.extend(_scenarios(lines))
    return [_trim(item) for item in dict.fromkeys(items) if _trim(item)]


def _heading_match(line: str, headings: Sequence[str]) -> tuple[bool, str]:
    stripped = _HEADING_MARKUP.sub("", line).strip()
    stripped = _EMPHASIS.sub("", stripped).strip()
    for heading in headings:
        pattern = re.compile(rf"^{re.escape(heading)}\s*[*_]*\s*(?::|-|–)?\s*(.*)$", re.I)
        match = pattern.match(stripped)
        if match is None:
            continue
        rest = _EMPHASIS.sub("", match.group(1)).strip()
        # "Acceptance criteria are tracked elsewhere" is a sentence, not a heading.
        if rest and not re.match(rf"^{re.escape(heading)}\s*[*_]*\s*[:\-–]", stripped, re.I):
            continue
        return True, rest
    return False, ""


def _section_items(
    lines: Sequence[str], start: int, headings: Sequence[str]
) -> tuple[int, list[str]]:
    """The list (or short paragraph) under a heading, and where reading stopped."""
    items: list[str] = []
    paragraph: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if _heading_match(line, headings)[0] or _looks_like_heading(line):
            break
        if not line.strip():
            if _section_ends(lines, index, has_list=bool(items), has_paragraph=bool(paragraph)):
                break
        elif _LIST_MARKER.match(line):
            items.append(_LIST_MARKER.sub("", line))
        elif items and line.startswith((" ", "\t")):
            items[-1] = f"{items[-1]} {line.strip()}"
        elif not items:
            paragraph.append(line.strip())
        else:
            break
        index += 1
    if not items and paragraph:
        items = [" ".join(paragraph)]
    return index, items


def _section_ends(lines: Sequence[str], index: int, *, has_list: bool, has_paragraph: bool) -> bool:
    """At a blank line: a paragraph ends there; a list goes on only if a list item follows."""
    if not has_list and not has_paragraph:
        return False
    if has_paragraph and not has_list:
        return True
    return index + 1 >= len(lines) or not _LIST_MARKER.match(lines[index + 1])


def _looks_like_heading(line: str) -> bool:
    return bool(re.match(r"^\s*(?:#{1,6}\s|h[1-6]\.\s)", line))


def _scenarios(lines: Sequence[str]) -> list[str]:
    scenarios: list[str] = []
    title: str | None = None
    steps: list[str] = []

    def flush() -> None:
        if steps:
            head = f"{title}: " if title else ""
            scenarios.append(head + " ".join(steps))

    for line in lines:
        scenario = _GHERKIN_SCENARIO.match(line)
        if scenario:
            flush()
            title = scenario.group(1).strip() or None
            steps = []
            continue
        if _GHERKIN_STEP.match(_CHECKBOX.sub("", _LIST_MARKER.sub("", line))):
            steps.append(" ".join(_LIST_MARKER.sub("", line).split()))
            continue
        if steps and not line.strip():
            flush()
            title, steps = None, []
    flush()
    return scenarios


def _question_summary(comment: IssueComment) -> str | None:
    body = " ".join(comment.body.split())
    if "?" not in body:
        return None
    asked = [match.group(0).strip() for match in _SENTENCE.finditer(body)]
    summary = " ".join(part for part in asked if part)
    unled = _QUESTION_LEAD.sub("", summary).strip()
    if unled and unled != summary:
        summary = unled[0].upper() + unled[1:]
    return _trim(_without_leading_mentions(summary, comment), MAX_SUMMARY)


def _without_leading_mentions(text: str, comment: IssueComment) -> str:
    """'@Maria is it in scope?' reads 'Is it in scope?': who was asked is shown apart."""
    names = sorted(
        {
            name
            for person in comment.mentions
            for name in (person.display_name, person.external_id)
            if name
        },
        key=len,
        reverse=True,
    )
    rest = text
    while lead := next(
        (name for name in names if rest.casefold().startswith(f"@{name}".casefold())), None
    ):
        rest = rest[len(lead) + 1 :].lstrip(" ,:;-–")
    if not rest or rest == text:
        return text
    return rest[0].upper() + rest[1:]


def _trim(text: str, limit: int = MAX_ITEM_TEXT) -> str:
    clean = " ".join(_CHECKBOX.sub("", text).split()).strip(" -–")
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
