"""When a check-in reply carries no status, read by rules before any model.

A reply without a status never counts as green. In a live run Elena, an exec with no
tickets, answered "Thanks, nothing from me today. No blockers on my side and no
open tickets or MRs." The evaluator's only rule was a handful of single words
("ok", "thanks"), so the model read the reply. It called it a status, the
coordinator rule took "No blockers" for team context, and her day was
recorded confirmed: her No-pod tile read green. An earlier run's "thanks, nothing from me"
had stayed a reply without a status only because the model happened to say so.

A reply reads as carrying no status when it is made only of:

- acknowledgements and greetings: "thanks", "ok", "got it", "hi", "have a nice
  weekend", an emoji;
- "nothing from me": "nothing from me today", "no updates", "nothing to report",
  "nothing new on my side";
- "no work of mine": "no open tickets or MRs", "nothing assigned to me";
- and, only for someone the tracker lists no open work for, "no blockers (on my
  side)", when the reply also says nothing from me or no work of mine.

Anything else is for the model: an issue key, a merge request, a number, or any
other word. So is "no blockers" from someone with open work, since "quiet day,
nothing blocking" is a status from a person with tasks, and a bare "no blockers"
from a coordinator, which counts as team context.
"""

from __future__ import annotations

import re

# Words a reply may keep around the phrases below and still say nothing: joiners
# and when or whose ("today", "on my side"). Never a word that can carry work,
# such as "it" ("on it"), "done", "fine" or "good".
_FILLER = frozenset(
    {
        "again",
        "also",
        "and",
        "as",
        "at",
        "atm",
        "but",
        "either",
        "end",
        "far",
        "for",
        "from",
        "here",
        "just",
        "me",
        "moment",
        "my",
        "nor",
        "now",
        "of",
        "on",
        "or",
        "really",
        "right",
        "side",
        "so",
        "the",
        "this",
        "time",
        "today",
        "too",
        "week",
        "yet",
    }
)
# Chat text may carry an emoji as ":name:" and a mention as "<@…>".
_CHAT_MARKUP = re.compile(r":[a-z0-9_+'-]+:|<@[^>]*>")
_WORD = re.compile(r"[a-z0-9']+")

_ACKNOWLEDGEMENT = re.compile(
    r"\b(?:thanks?(?:\s+(?:a\s+lot|so\s+much|again))?|thank\s+you(?:\s+(?:so\s+much|very\s+much|again))?"
    r"|(?:thanks?|thank\s+you)\s+for\s+(?:asking|checking(?:\s+in)?|the\s+(?:reminder|ping|nudge))"
    r"|thx|ty|tyvm|cheers|ok(?:ay)?|kk?|sure|noted|got\s+it|will\s+do|sounds\s+good"
    r"|np|no\s+problem|no\s+worries|hi|hello|hey|good\s+(?:morning|afternoon|evening)|morning"
    r"|(?:have|enjoy)\s+(?:a|the|your)\s+(?:good\s+|nice\s+|great\s+)?(?:day|weekend|evening|one)"
    r"|see\s+you|talk\s+soon|bye)\b"
)
# A bare "nothing" is not one: it may answer "is anything blocking you?".
_MINE = r"(?:from|on|for)\s+(?:me|my\s+(?:side|end|part))"
_NOTHING_FROM_ME = re.compile(
    r"\b(?:(?:i\s+have|i've\s+got|i've|i\s+got|there's|there\s+is)\s+)?"
    rf"(?:nothing\s+(?:new\s+)?{_MINE}"
    rf"|no\s+(?:new\s+|real\s+)?(?:updates?|news|status(?:\s+updates?)?)(?:\s+{_MINE})?"
    r"|nothing\s+(?:new\s+|else\s+|much\s+)?to\s+(?:report|update|share|add|say)"
    r"|nothing\s+new|not\s+much\s+(?:new|to\s+report|from\s+me))\b"
)
# "issues" alone often means problems ("no issues"), so it counts as work only
# when qualified ("no open issues", "no issues assigned").
_WORK = (
    r"(?:tickets?|tasks?|stories|story|work\s+items?|mrs?|prs?"
    r"|merge\s+requests?|pull\s+requests?)"
)
_ANY_WORK = rf"(?:{_WORK}|issues?)"
_QUALIFIER = r"(?:open|active|assigned|current)\s+"
_WORK_LIST = rf"(?:\s*(?:,|/|or|nor|and)\s*(?:{_QUALIFIER})?{_ANY_WORK})*"
_ASSIGNED = r"(?:\s+(?:assigned|open))?(?:\s+(?:to|for|on)\s+me)?"
_NO_WORK_OF_MINE = re.compile(
    rf"\b(?:(?:i\s+have|i've\s+got|i've|i\s+got|there\s+are)\s+)?"
    rf"(?:no\s+{_QUALIFIER}{_ANY_WORK}{_WORK_LIST}{_ASSIGNED}"
    rf"|no\s+{_WORK}{_WORK_LIST}{_ASSIGNED}"
    rf"|no\s+{_ANY_WORK}{_WORK_LIST}\s+(?:assigned|open)(?:\s+(?:to|for|on)\s+me)?"
    r"|nothing\s+(?:assigned|open)(?:\s+(?:to|for|on)\s+me)?|nothing\s+on\s+my\s+plate)\b"
)
_NO_BLOCKERS = re.compile(
    r"\b(?:no(?:thing)?\s+(?:new\s+|open\s+)?(?:blockers?|blocking|impediments?)"
    rf"|nothing(?:'s|\s+is)?\s+blocking(?:\s+me)?|not\s+blocked)(?:\s+{_MINE})?\b"
)


def reads_without_status(text: str, *, has_own_work: bool | None) -> bool:
    """Whether ``text`` (one reply, or all of a check-in's messages) carries no status.

    ``has_own_work`` is whether the tracker lists open work assigned to the
    person; None when it is not known, which reads as if they had some. Only
    a reply saying no blockers next to nothing from me depends on it.
    """
    rest = _normalized(text)
    said_nothing = False
    for phrase in (_NO_WORK_OF_MINE, _NOTHING_FROM_ME):
        rest, count = phrase.subn(" ", rest)
        said_nothing = said_nothing or count > 0
    if said_nothing and has_own_work is False:
        rest = _NO_BLOCKERS.sub(" ", rest)
    rest = _ACKNOWLEDGEMENT.sub(" ", rest)
    return all(word in _FILLER for word in _WORD.findall(rest))


def depends_on_own_work(text: str) -> bool:
    """Whether reading ``text`` needs to know if the person has open work.

    Lets a caller read the tracker only for the replies it decides: "nothing
    from me, no blockers" carries no status only from someone with no work.
    """
    return reads_without_status(text, has_own_work=False) and not reads_without_status(
        text, has_own_work=True
    )


def _normalized(text: str) -> str:
    lowered = text.casefold().replace("\u2019", "'")
    return " ".join(_CHAT_MARKUP.sub(" ", lowered).split())
