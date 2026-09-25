"""An OpenAI-compatible endpoint that fakes the model with rules, not weights.

Enough behaviour for the local stack to be *demonstrable* without a key: a
status reply is parsed into structured signals by pattern-matching the phrases
people actually type ("blocked on X", "waiting for Y", "slipping two days",
"need Priya to review"), and a narrative brief prompt gets prose assembled from
the facts the prompt already carries rather than an echo of the prompt.

Nothing here is a model. It is deliberately legible so a demo answer can be
traced to the rule that produced it, and so swapping in a real endpoint
(``scripts/use-real-llm.sh``) is a visible upgrade rather than a silent one.
"""

from __future__ import annotations

import json
import re
from time import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

app = FastAPI(title="OpenProgram Mock LLM")

# Markers the status-parsing prompts put in front of the developer's own words.
_REPLY_MARKERS = ("Latest reply:", "Reply:")

# Clause openers that introduce a blocker. Ordered longest-first so
# "still waiting on" wins over "waiting on".
_BLOCKER_OPENERS = (
    "still blocked on",
    "still blocked by",
    "still waiting on",
    "still waiting for",
    "blocked on",
    "blocked by",
    "waiting on",
    "waiting for",
    "stuck on",
    "held up by",
    "can't proceed without",
    "cannot proceed without",
    "need help with",
)

# Phrases that explicitly deny any blocker, checked before the openers so
# "nothing blocking" is never mistaken for one.
_NO_BLOCKER_PHRASES = (
    "no blockers",
    "no blocker",
    "nothing blocking",
    "nothing is blocking",
    "not blocked",
    "no issues",
    "all clear",
    "unblocked",
)

_RESOLVED_PHRASES = (
    "unblocked",
    "blocker cleared",
    "blocker is cleared",
    "no longer blocked",
    "that's resolved",
    "now resolved",
)

_WORD_NUMBERS = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# "slipping 2 days", "eta +3 days", "two days late", "pushed by a day"
_ETA_PATTERNS = (
    re.compile(
        r"(?:slip(?:ping|s|ped)?|late|delay(?:ed)?|push(?:ed)?(?:\s+out)?|"
        r"behind|over)\D{0,14}?(\d+|\w+)\s*(?:more\s+)?(?:day|days)",
        re.IGNORECASE,
    ),
    re.compile(r"eta\D{0,12}?\+\s*(\d+)\s*(?:day|days)", re.IGNORECASE),
    re.compile(
        r"(\d+|\w+)\s*(?:day|days)\s*(?:of\s+)?(?:slip|delay|late|behind)",
        re.IGNORECASE,
    ),
)

# "need Priya to review", "waiting on @sam for the schema", "ask Tom to sign off"
_REQUEST_PATTERN = re.compile(
    r"\b(?:need|needs|needed|waiting\s+on|waiting\s+for|asked?|chasing)\s+"
    r"@?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
    r"(?:\s+(?:to|for)\s+([^.;,]{3,60}))?",
)

_ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")

# The parse prompt lists blockers already open for this person as
# "- [B1] description (ISSUE-1)". Reusing that exact wording is what keeps a
# restated blocker from being minted as a second, near-duplicate row.
_PRIOR_BLOCKER_PATTERN = re.compile(
    r"^-\s*\[(B\d+)\]\s*(.+?)(?:\s*\(([A-Z][A-Z0-9]{1,9}-\d+)\))?\s*$",
    re.MULTILINE,
)

# Words too common to signal that two blocker statements are the same one.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "still",
        "that",
        "the",
        "their",
        "to",
        "up",
        "was",
        "we",
        "with",
        "yet",
        "not",
        "no",
        "my",
    }
)

_DONE_PATTERN = re.compile(
    r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b[^.;]{0,40}?\b(?:is\s+)?(?:done|merged|shipped|complete)\b",
    re.IGNORECASE,
)

_REQUEST_KINDS = (
    ("review", ("review", "approve", "sign off", "sign-off", "look at")),
    ("dependency", ("provision", "deploy", "fix", "unblock", "access", "credential")),
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/readiness")
async def readiness() -> dict[str, str]:
    """Mirrors the LiteLLM gateway path the backend readiness probe calls."""
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = _last_user_content(payload)
    system = _system_content(payload)
    if _json_mode(payload):
        content = _status_signals_json(prompt)
    elif _is_brief_prompt(system, prompt):
        content = _brief_narrative(prompt)
    elif "Developer:" in prompt:
        content = (
            "Quick check-in: what moved since yesterday, what is planned today, "
            "and is anything blocking you?"
        )
    else:
        content = _plain_summary(prompt)
    prompt_tokens = max(1, len(prompt.split()))
    completion_tokens = max(1, len(content.split()))
    return {
        "id": f"chatcmpl-{uuid4()}",
        "object": "chat.completion",
        "created": int(time()),
        "model": payload.get("model", "local-gpt"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _last_user_content(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return str(message["content"])
    return ""


def _system_content(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "system":
                content = message.get("content")
                if isinstance(content, str):
                    return content
    return ""


def _json_mode(payload: dict[str, Any]) -> bool:
    response_format = payload.get("response_format")
    return isinstance(response_format, dict) and response_format.get("type") == "json_object"


# --------------------------------------------------------------------------
# status signal extraction
# --------------------------------------------------------------------------


def _status_signals_json(prompt: str) -> str:
    """One object satisfying both the status parser and the clarification check.

    Blockers, ETA slips, cross-person asks, and "issue is done" claims are
    lifted out of the reply text by the rules above, so what a person types in
    the demo chat is what shows up in their status. A blocker the person is
    restating is reported with the wording already on file, and one they say is
    cleared is reported by its handle.
    """
    reply = _reply_text(prompt) or prompt.strip()
    priors = _prior_blockers(prompt)
    raw_blockers = _extract_blockers(reply)
    blocker_details = _reconcile_with_priors(raw_blockers, priors)
    blockers = [detail["description"] for detail in blocker_details]
    eta_change_days = _extract_eta_change(reply)
    requests = _extract_requests(reply, blockers)
    issue_updates = _extract_issue_updates(reply)
    resolved = _resolved_handles(reply, priors)
    signals = {
        "progress_note": _progress_note(reply) or "Status update received",
        "blockers": blockers,
        "blocker_details": blocker_details,
        "resolved_blocker_ids": resolved,
        "eta_change_days": eta_change_days,
        # Both questions count as answered whenever the reply speaks to them at
        # all, which is what stops the demo looping on clarifications.
        "blockers_answered": bool(blockers) or _denies_blockers(reply) or bool(resolved),
        "eta_answered": eta_change_days is not None or not blockers,
        "requests": requests,
        "issue_updates": issue_updates,
    }
    return json.dumps(
        {
            **signals,
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": signals,
        }
    )


def _reply_text(prompt: str) -> str:
    for marker in _REPLY_MARKERS:
        _, separator, tail = prompt.partition(marker)
        if separator:
            return tail.strip()
    return ""


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _denies_blockers(reply: str) -> bool:
    lowered = reply.lower()
    return any(phrase in lowered for phrase in _NO_BLOCKER_PHRASES)


def _extract_blockers(reply: str) -> list[str]:
    """Pull one blocker per clause introduced by a blocker opener."""
    blockers: list[str] = []
    for sentence in _sentences(reply):
        lowered = sentence.lower()
        if any(phrase in lowered for phrase in _RESOLVED_PHRASES):
            continue
        for clause in re.split(r";|,\s+and\s+|\s+and\s+also\s+", sentence):
            blocker = _blocker_from_clause(clause)
            if blocker and blocker not in blockers:
                blockers.append(blocker)
    return blockers


def _blocker_from_clause(clause: str) -> str | None:
    lowered = clause.lower()
    if any(phrase in lowered for phrase in _NO_BLOCKER_PHRASES):
        return None
    for opener in _BLOCKER_OPENERS:
        index = lowered.find(opener)
        if index == -1:
            continue
        tail = clause[index + len(opener) :].strip(" :-–.!")
        if len(tail) < 3:
            return None
        return _titlecase_first(tail)
    return None


def _titlecase_first(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return stripped
    return stripped[0].upper() + stripped[1:]


def _prior_blockers(prompt: str) -> list[dict[str, Any]]:
    """Blockers already open for this person, as the prompt lists them."""
    return [
        {
            "handle": match.group(1),
            "description": match.group(2).strip(),
            "issue_key": match.group(3),
        }
        for match in _PRIOR_BLOCKER_PATTERN.finditer(prompt)
    ]


def _content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9-]+", text.lower()) if word not in _STOPWORDS} - {
        ""
    }


def _match_prior(candidate: str, priors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The open blocker a restatement refers to, or None for a new one.

    Two statements are treated as the same blocker when they share at least two
    distinctive words, or when one contains the other -- enough to catch
    "the sandbox credentials" against "3-D Secure sandbox credentials still not
    provisioned" without collapsing unrelated blockers.
    """
    words = _content_words(candidate)
    best: dict[str, Any] | None = None
    best_score = 0
    for prior in priors:
        prior_words = _content_words(prior["description"])
        overlap = len(words & prior_words)
        contained = (
            candidate.lower() in prior["description"].lower()
            or prior["description"].lower() in candidate.lower()
        )
        score = overlap + (2 if contained else 0)
        if score > best_score and (overlap >= 2 or contained):
            best, best_score = prior, score
    return best


def _reconcile_with_priors(
    blockers: list[str], priors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Report restated blockers with their existing wording and issue key."""
    details: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in blockers:
        prior = _match_prior(candidate, priors)
        description = prior["description"] if prior else candidate
        if description in seen:
            continue
        seen.add(description)
        details.append(
            {
                "description": description,
                "issue_key": (prior and prior["issue_key"]) or _first_issue_key(candidate),
                "pod_id": None,
            }
        )
    return details


def _resolved_handles(reply: str, priors: list[dict[str, Any]]) -> list[str]:
    """Handles of prior blockers the reply says are cleared."""
    handles: list[str] = []
    for sentence in _sentences(reply):
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in _RESOLVED_PHRASES):
            continue
        prior = _match_prior(sentence, priors)
        if prior is not None and prior["handle"] not in handles:
            handles.append(prior["handle"])
    return handles


def _extract_eta_change(reply: str) -> int | None:
    for pattern in _ETA_PATTERNS:
        match = pattern.search(reply)
        if match is None:
            continue
        days = _as_days(match.group(1))
        if days is not None:
            return days
    return None


def _as_days(raw: str) -> int | None:
    token = raw.strip().lower()
    if token.isdigit():
        value = int(token)
        return value if 0 < value <= 90 else None
    return _WORD_NUMBERS.get(token)


def _extract_requests(reply: str, blockers: list[str]) -> list[dict[str, Any]]:
    """Cross-person asks: a named person plus what is wanted from them."""
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _REQUEST_PATTERN.finditer(reply):
        name = match.group(1).strip()
        if not name or name.lower() in _WORD_NUMBERS or name in seen:
            continue
        seen.add(name)
        note = (match.group(2) or "").strip() or _default_note(blockers)
        requests.append({"raw_name": name, "kind": _request_kind(note), "note": note})
    return requests


def _default_note(blockers: list[str]) -> str:
    return blockers[0] if blockers else "follow-up needed"


def _request_kind(note: str) -> str:
    lowered = note.lower()
    for kind, keywords in _REQUEST_KINDS:
        if any(keyword in lowered for keyword in keywords):
            return kind
    return "input"


def _extract_issue_updates(reply: str) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for match in _DONE_PATTERN.finditer(reply):
        updates.append(
            {
                "issue_key": match.group(1),
                "claimed_done": True,
                "claimed_state": "done",
                "note": match.group(0).strip(),
            }
        )
    return updates


def _first_issue_key(text: str) -> str | None:
    match = _ISSUE_KEY_PATTERN.search(text)
    return match.group(1) if match else None


def _progress_note(reply: str) -> str:
    """The first sentence that is progress rather than a blocker statement.

    A bare "nothing blocking" is skipped, but a longer sentence that happens to
    mention being unblocked is progress and is kept. When the whole reply is
    blockers, the note says so rather than repeating text that already lives in
    its own field.
    """
    for sentence in _sentences(reply):
        if _blocker_from_clause(sentence) is not None:
            continue
        if _denies_blockers(sentence) and len(sentence.split()) < 6:
            continue
        return sentence.rstrip(".")
    return "Reported blockers only; no separate progress note." if reply.strip() else ""


# --------------------------------------------------------------------------
# narrative briefs
# --------------------------------------------------------------------------


def _is_brief_prompt(system: str, prompt: str) -> bool:
    return "brief" in system.lower() or "brief" in prompt.lower()[:200]


def _brief_narrative(prompt: str) -> str:
    """Prose assembled from the brief context the caller already built.

    The context has a fixed shape -- a headline, a rollup line, then
    ``Recent activity:`` followed by one bullet per feed item -- and the
    provider prepends the title, so the headline can arrive twice. Everything
    before the activity marker is treated as heading material: the first line
    is the headline, the last is the rollup.
    """
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    if not lines:
        return "No delivery activity was recorded in this window."

    bullets = [line.lstrip("-\u2022").strip() for line in lines if _is_bullet(line)]
    heading = [line for line in lines if not _is_bullet(line) and not _is_activity_marker(line)]
    headline = heading[0].rstrip(".") if heading else ""
    rollup = heading[-1].rstrip(".") if len(heading) > 1 else ""

    sentences = [f"{headline}."] if headline else []
    if rollup and rollup.lower() != headline.lower():
        sentences.append(f"{rollup}.")

    if not bullets:
        sentences.append("No delivery activity was recorded in this window.")
        return " ".join(sentences)

    counts = _bullet_counts(bullets)
    observed = [f"{count} {label}" for label, count in counts.items() if count]
    if observed:
        sentences.append(f"The window shows {_join_phrases(observed)}.")
    if counts.get("open risk signal(s)"):
        sentences.append("Each risk signal drills back to the owning work item and its owner.")
    if counts.get("cross-person request(s)"):
        sentences.append("Cross-person requests are each waiting on a named counterpart.")
    return " ".join(sentences)


def _is_activity_marker(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith(("recent activity", "no recent activity"))


def _is_bullet(line: str) -> bool:
    return line.lstrip().startswith(("-", "\u2022"))


def _bullet_counts(bullets: list[str]) -> dict[str, int]:
    """Count feed bullets by the phrasing the feed service emits."""
    counts = {
        "check-in update(s)": 0,
        "work item transition(s)": 0,
        "pull request event(s)": 0,
        "commit(s)": 0,
        "open risk signal(s)": 0,
        "cross-person request(s)": 0,
    }
    for bullet in bullets:
        lowered = bullet.lower()
        if lowered.startswith("check-in"):
            counts["check-in update(s)"] += 1
        elif lowered.startswith("risk"):
            counts["open risk signal(s)"] += 1
        elif lowered.startswith("cross-person"):
            counts["cross-person request(s)"] += 1
        elif lowered.startswith("pr "):
            counts["pull request event(s)"] += 1
        elif lowered.startswith("commit "):
            counts["commit(s)"] += 1
        elif "moved" in lowered:
            counts["work item transition(s)"] += 1
    return counts


def _join_phrases(phrases: list[str]) -> str:
    if len(phrases) == 1:
        return phrases[0]
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def _plain_summary(prompt: str) -> str:
    first = _sentences(prompt)
    return first[0][:200] if first else "No content to summarize."
