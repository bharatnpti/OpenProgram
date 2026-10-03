"""Slack message text as the person typed it.

Slack delivers a message's ``text`` in its own markup: an address someone types
arrives as ``<mailto:a@b.io|a@b.io>``, a mention as ``<@U123>``, a link as
``<https://...|label>``, and ``&``, ``<`` and ``>`` as HTML entities. Nothing
past the adapter should have to know that, so inbound text is unwrapped here.
A user mention keeps the chat id (``@U123``), which is what names a member.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"<([^<>\n]+)>")


def plain_text(text: str) -> str:
    """Unwrap Slack's angle-bracket markup and entities into plain text."""
    unwrapped = _TOKEN.sub(lambda match: _unwrap(match.group(1)), text)
    return unwrapped.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _unwrap(token: str) -> str:
    target, _, label = token.partition("|")
    if target.startswith("@"):
        return f"@{target[1:]}"
    if target.startswith("#"):
        return f"#{label or target[1:]}"
    if target.startswith("!"):
        # <!here>, <!subteam^S1|@payments>, <!date^...|fallback>
        return label or f"@{target[1:].split('^', 1)[0]}"
    if target.startswith("mailto:"):
        return target.removeprefix("mailto:")
    if target.startswith("tel:"):
        return label or target.removeprefix("tel:")
    if not label or label == target or target.endswith(f"//{label}"):
        return target
    return f"{label} ({target})"
