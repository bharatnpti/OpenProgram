"""Model-written words made safe before OpenProgram sends them to a person.

Application code decides every message OpenProgram sends; the model only
drafts some of the words: a follow-up question, the short ask a teammate is
told about, a composed check-in. Those words can carry whatever the model read
in a reply or a tracker title, including text written to steer it. Coming from
the bot, a link or a mention is trusted, so model-drafted words never carry
one: a link, or chat markup in angle brackets (mentions, broadcast notices,
links with a disguised label), either sends the message's fixed fallback
instead or is taken out.
"""

from __future__ import annotations

import re

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
