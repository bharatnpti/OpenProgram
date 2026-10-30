"""Model-written words made safe before OpenProgram sends them to a person or shows them.

Application code decides every message OpenProgram sends; the model only
drafts some of the words: a follow-up question, the short ask a teammate is
told about, a composed check-in. Those words can carry whatever the model read
in a reply or a tracker title, including text written to steer it. Coming from
the bot, a link or a mention is trusted, so model-drafted words never carry
one: a link, or chat markup in angle brackets (mentions, broadcast notices,
links with a disguised label), either sends the message's fixed fallback
instead or is taken out. Nor do they ever say how OpenProgram works inside:
text that names a tool, talks about its own instructions or repeats a system
message is replaced by fixed text too (docs/guardrails.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from core.domain.prompt_safety import MODEL_RULES, UNTRUSTED_TAG

LINK_REMOVED = "(link removed)"

_LINK = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)
_MARKUP = re.compile(r"<([^<>\n]+)>")


def carries_link_or_markup(text: str) -> bool:
    """Whether the text holds a link or angle-bracket chat markup."""
    return bool(_LINK.search(text) or _MARKUP.search(text))


def scrubbed(text: str, *, max_chars: int) -> str:
    """The text on one line with links and chat markup taken out, cut to ``max_chars``."""
    unwrapped = _MARKUP.sub(lambda match: _markup_words(match.group(1)), text)
    flat = " ".join(_LINK.sub(LINK_REMOVED, unwrapped).split())
    if len(flat) <= max_chars:
        return flat
    if max_chars <= 1:
        return flat[:max_chars]
    return f"{flat[: max_chars - 1].rstrip()}…"


def _markup_words(token: str) -> str:
    target, _, label = token.partition("|")
    if _LINK.match(target):
        return LINK_REMOVED
    # A mention or notice keeps its readable label, never the markup itself.
    return label.lstrip("@#!")


# What only OpenProgram's insides are called: the tools a model can call, and the
# fence its input arrives in. A test checks every tool defined in core is here.
INTERNAL_NAMES = frozenset(
    {
        "delivery_forecast",
        "delivery_impact",
        "fetch_conversation_history",
        "fetch_git_activity",
        "fetch_issue_tracker_context",
        "graph_neighbors",
        "jira_hygiene",
        "open_risks",
        "pod_blockers",
        "pod_checkins",
        "portfolio_flow",
        "portfolio_heatmap",
        "recent_facts",
        "release_readiness",
        "search_graph_nodes",
        "status_reasons",
        "team_profiles",
        "workstream_flow",
        "workstream_progress",
        UNTRUSTED_TAG,
    }
)
INTERNALS_ANSWER = (
    "I can only answer questions about your programme's delivery: status, risks, "
    "blockers and who is working on what."
)
_INTERNAL_NAME = re.compile(r"\b(?:" + "|".join(sorted(INTERNAL_NAMES)) + r")\b", re.IGNORECASE)
# Text that talks about the model's own instructions or tools.
_SELF_DISCLOSURE = re.compile(
    r"\bsystem (?:prompt|message)s?\b"
    r"|\bmy (?:instructions|prompt|system message|tools|rules)\b"
    r"|\bi(?: was| am|'m| have been|'ve been) (?:instructed|told|programmed|configured) to\b"
    r"|\b(?:tools|functions) (?:i|that i) (?:have|use|can call|call)\b",
    re.IGNORECASE,
)
# A question about the assistant itself rather than about delivery.
_ABOUT_INTERNALS = re.compile(
    r"\b(?:system|hidden|initial|original) (?:prompt|message|instructions)\b"
    r"|\byour (?:instructions|prompts?|rules|guidelines|configuration|tools|functions)\b"
    r"|\b(?:what|which) (?:tools|functions) (?:do|can|did|are) you\b"
    r"|\blist (?:all |of )*(?:your |the )?(?:tools|functions)\b"
    r"|\bhow (?:do|does) (?:you|openprogram|this (?:bot|assistant)) work\b(?! out)"
    r"|\bhow (?:were|are) you (?:built|made|configured|prompted|trained|set up)\b"
    r"|\b(?:what|which) (?:ai )?(?:model|llm|language model) (?:are|do|did|does) you\b"
    r"|\b(?:what|which) (?:llm|language model)\b"
    r"|\bare you (?:gpt|chatgpt|claude|gemini|an? (?:ai|llm|language model|bot))\b"
    r"|\b(?:ignore|disregard|forget) (?:all |any |the |your )*"
    r"(?:previous |prior |above |earlier )?(?:instructions|rules|prompts?)\b"
    r"|\b(?:developer|jailbreak|dan) mode\b"
    r"|\b(?:repeat|print|show|reveal) (?:me )?(?:the |your )?(?:text|words|prompt|instructions) "
    r"(?:above|before)\b",
    re.IGNORECASE,
)
# Eight words in a row from a system message, in a model's output, are a leak.
# A quoted example ('Partial updates: Omar Haddad, ...') is what the model is
# asked to write, so it is left out of the comparison.
_ECHO_WORDS = 8
_WORD = re.compile(r"[a-z0-9']+")
_QUOTED_EXAMPLE = re.compile(r"(?<![A-Za-z])'[^']+?'(?![A-Za-z])|\"[^\"]+?\"")


def asks_about_internals(question: str) -> bool:
    """Whether a question asks about the assistant itself: its prompt, tools, model or rules."""
    return bool(_ABOUT_INTERNALS.search(question))


def reveals_internals(text: str, *, prompts: Iterable[str] = ()) -> bool:
    """Whether model output names a tool, talks about its instructions, or echoes a prompt."""
    if _INTERNAL_NAME.search(text) or _SELF_DISCLOSURE.search(text):
        return True
    words = _WORD.findall(text.casefold())
    if len(words) < _ECHO_WORDS:
        return False
    last = len(words) - _ECHO_WORDS + 1
    runs = {tuple(words[index : index + _ECHO_WORDS]) for index in range(last)}
    for prompt in (*prompts, MODEL_RULES):
        prompt_words = _WORD.findall(_QUOTED_EXAMPLE.sub(" ", prompt).casefold())
        for index in range(len(prompt_words) - _ECHO_WORDS + 1):
            if tuple(prompt_words[index : index + _ECHO_WORDS]) in runs:
                return True
    return False
