"""Microsoft Teams channel messages, posted through a channel webhook.

The payload is an Adaptive Card inside a message, which both a channel's
Workflows webhook and the older incoming-webhook connector accept.
"""

from __future__ import annotations

from collections.abc import Sequence


def teams_card_payload(
    title: str,
    lines: Sequence[str],
    *,
    link: tuple[str, str] | None = None,
) -> dict[str, object]:
    """A message carrying one Adaptive Card: a bold title, then one text block per line.

    ``link`` is an optional (label, url) action button under the text.
    """
    body: list[dict[str, object]] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    body.extend(
        {"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"} for line in lines
    )
    card: dict[str, object] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if link is not None:
        label, url = link
        card["actions"] = [{"type": "Action.OpenUrl", "title": label, "url": url}]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }
