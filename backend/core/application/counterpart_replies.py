"""Reading a counterpart's reply to a request DM without the model.

The person asked for a review answers in a word or a line: "approved",
"LGTM, one nit", "Merged", "CHK-17 should merge tomorrow". Those were sent to
the model with a free-text prompt, and a reply it did not wrap as bare JSON
fell back to "acknowledged" with nothing kept: Omar's "should merge tomorrow"
lost its ETA, and nobody told Zoe. Short replies are read here first; only
what this cannot read goes to the model.

The ETA kept is a phrase from a closed vocabulary ("tomorrow", "by Friday",
"by end of day"), never the reply's own words, so a notice built from it
relays no reply text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.domain.cross_person import CrossPersonRequestStatus

__all__ = ["CounterpartReplyReading", "eta_phrase", "read_counterpart_reply"]


@dataclass(frozen=True, kw_only=True)
class CounterpartReplyReading:
    status: CrossPersonRequestStatus
    eta: str | None = None


_WEEKDAYS = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "wednesday": "Wednesday",
    "wed": "Wednesday",
    "thursday": "Thursday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "friday": "Friday",
    "fri": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}
_WEEKDAY = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_NUMBER_WORDS = {"a": "1", "an": "1", "one": "1", "two": "2", "three": "3", "few": "a few"}
_ETA_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blater today\b"), "later today"),
    (re.compile(r"\bthis (afternoon|evening|morning)\b"), "this {0}"),
    (re.compile(r"\btonight\b"), "tonight"),
    (re.compile(r"\b(?:tomorrow|tmrw|tmr)\b"), "tomorrow"),
    (re.compile(r"\bnext (week|sprint)\b"), "next {0}"),
    (re.compile(rf"\bnext ({_WEEKDAY})\b"), "next {weekday}"),
    (re.compile(r"\b(?:by )?(?:eod|end of (?:the )?day)\b"), "by end of day"),
    (re.compile(r"\b(?:by )?(?:eow|end of (?:the )?week)\b"), "by end of week"),
    (re.compile(r"\b(?:by )?end of (?:the )?sprint\b"), "by end of the sprint"),
    (re.compile(rf"\bby ({_WEEKDAY})\b"), "by {weekday}"),
    (re.compile(rf"\b(?:on |this )?({_WEEKDAY})\b"), "on {weekday}"),
    (
        re.compile(r"\bin (\d+|a|an|one|two|three|a few) (min(?:ute)?s?|hours?|hrs?|days?)\b"),
        "in {0} {1}",
    ),
    (re.compile(r"\b(?:soon|shortly)\b"), "soon"),
    (re.compile(r"\btoday\b"), "today"),
)
# "today" says when only alongside a future verb ("will finish today");
# "merged today" is done.
_PRESENT_ONLY = frozenset({"today"})
_FUTURE = re.compile(
    r"\b(?:will|shall|should|going to|gonna|plan(?:ning)? to|aim(?:ing)? to|hope to"
    r"|expect(?:ing)? to|about to|eta|want to|need to|have to)\b|'ll\b"
)
_NOT_DONE = re.compile(
    r"\b(?:not (?:done|yet|ready|finished|approved|merged|resolved|complete|completed)"
    r"|haven'?t|hasn'?t|didn'?t|can'?t|cannot|won'?t|isn'?t|aren'?t|wasn'?t"
    r"|still (?:working|reviewing|waiting|on it|looking|in progress)"
    r"|blocked|need(?:s)? (?:more time|changes)|changes requested|request(?:ed)? changes"
    r"|in progress|wip)\b"
)
_DONE = re.compile(
    r"(?:\b(?:done|resolved|completed?|finished|reviewed|approved?|lgtm|merged|sent|shared"
    r"|provided|unblocked|fixed|shipped|released|deployed|landed|looks good|ship ?it)\b"
    r"|(?<!\w)\+1(?!\w)|:\+1:|:white_check_mark:|\N{THUMBS UP SIGN}|\N{WHITE HEAVY CHECK MARK})"
)
_ACKNOWLEDGE = re.compile(
    r"^(?:ok(?:ay)?|ack(?:nowledged)?|sure|yes|yep|yeah|noted|got it|will do|on it|looking"
    r"|on my list|roger|k|kk|thanks|thank you|ty)[.!]*$"
    r"|\b(?:i'?ll take|i will take|i'?ll look|i will look|i'?ll check|on it|will review"
    r"|can review|taking a look|having a look|will have a look|looking into it)\b"
)


def eta_phrase(text: str) -> str | None:
    """When a reply says the work will be done, as a fixed phrase; None if it does not say."""
    normalized = _normalized(text)
    future = _FUTURE.search(normalized) is not None
    for pattern, template in _ETA_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        if template in _PRESENT_ONLY and not future:
            continue
        return _render(template, match.groups())
    return None


def read_counterpart_reply(text: str) -> CounterpartReplyReading | None:
    """Resolved, acknowledged (with its ETA, if it gives one), or None to ask the model.

    Done words resolve unless the reply also says the work is still ahead
    ("will be done by Friday") or not finished ("not approved yet"). A when
    statement, or a future verb, acknowledges with its ETA; so does a plain
    "on it". Anything else is left to the model.
    """
    normalized = _normalized(text)
    if not normalized:
        return None
    eta = eta_phrase(normalized)
    not_done = _NOT_DONE.search(normalized) is not None
    ahead = eta is not None or _FUTURE.search(normalized) is not None
    if _DONE.search(normalized) and not (not_done or ahead):
        return CounterpartReplyReading(status=CrossPersonRequestStatus.RESOLVED)
    if eta is not None or not_done or ahead or _ACKNOWLEDGE.search(normalized):
        return CounterpartReplyReading(status=CrossPersonRequestStatus.ACKNOWLEDGED, eta=eta)
    return None


def _normalized(text: str) -> str:
    flat = " ".join(text.casefold().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").split())
    return flat.strip(" \t.,;:!?")


def _render(template: str, groups: tuple[str | None, ...]) -> str:
    values = [value or "" for value in groups]
    if "{weekday}" in template:
        return template.format(weekday=_WEEKDAYS.get(values[0], values[0].title()))
    if template.startswith("in {0}"):
        amount = _NUMBER_WORDS.get(values[0], values[0])
        unit = values[1]
        unit = {"hr": "hour", "hrs": "hours", "min": "minute", "mins": "minutes"}.get(unit, unit)
        if amount == "1":
            unit = unit.rstrip("s")
        elif not unit.endswith("s"):
            unit = f"{unit}s"
        return f"in {amount} {unit}"
    return template.format(*values)
