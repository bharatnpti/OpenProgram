"""A deterministic check of a model-written brief against its facts (N38, N39).

The prompt asks the model to say only what the facts say; this makes sure,
sentence by sentence, against the ``BriefFacts`` the brief was written from:

- A sentence naming an issue the facts do not name (another pod's, or one
  that does not exist) or a person they do not name is dropped.
- A sentence saying something was rescheduled, postponed, deferred or
  cancelled is dropped unless a fact uses the word; one saying something
  slipped or was delayed, unless an ETA moved later in the window.
- A sentence calling an issue completed or done that the tracker does not
  show done (a merged merge request on an open ticket) is replaced by what
  the facts say of each issue it names.
- A sentence saying there were no blockers when a check-in of the window
  reported one, or while one is open, is replaced by the blocker sentence.
- A sentence giving a status count the facts do not give is replaced by the
  status sentence.

When blockers were reported or are open and no sentence left mentions them,
the blocker sentence is added. No model call, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.application.brief_facts import (
    SENTENCE_ISSUE_KEY,
    BriefFacts,
    IssueFact,
    issue_sentence,
)

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\s*\n+\s*")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

_COMPLETION = re.compile(
    r"\b(?:complet(?:e|ed|es|ing|ion)|done|finish(?:ed|es|ing)?|deliver(?:ed|s|y|ies)?"
    r"|shipped|closed|wrapped\s+up|accomplish(?:ed|ment|ments)?|achiev(?:ed|ement|ements)"
    r"|implemented|fixed|success(?:ful|fully)?|succeeded)\b",
    re.IGNORECASE,
)
# Words just before a completion word that say it has not happened yet.
_NEGATED_BEFORE = re.compile(
    r"(?:\bnot|n't|\bnever|\byet\s+to\s+be|\bno\s+longer|\bawait(?:s|ing)?|\bpending"
    r"|\btowards?|\buntil|\bbefore)\s+(?:[\w-]+\s+){0,2}$",
    re.IGNORECASE,
)
# Words that say an issue is not done: the tracker's own view, in prose.
_STILL_OPEN = re.compile(
    r"\bopen\b|\bnot\s+(?:yet\s+)?(?:done|complete|completed|closed|finished|merged)\b"
    r"|\bin\s+progress\b|\bto\s+do\b|\b(?:in|under|awaiting)\s+review\b|\bpending\b"
    r"|\bwaiting\b|\bblocked\b|\bunderway\b|\bunmerged\b",
    re.IGNORECASE,
)
_OPEN_REACH = 120
_NO_BLOCKERS = re.compile(
    r"\b(?:no|zero|without(?:\s+any)?)\s+(?:[\w-]+\s+){0,2}?blockers?\b"
    r"|\bblocker[- ]free\b|\bnone\b(?:\s+[\w'-]+){0,5}?\s+blockers?\b"
    r"|\b(?:no\s+one|nobody|not)\s+(?:is\s+|was\s+|were\s+)?blocked\b",
    re.IGNORECASE,
)
# A clause that says what became of the blockers, not that there were none.
# Within one clause: 'no blockers, and a dependency was resolved' denies them.
_BLOCKERS_ACCOUNTED = re.compile(
    r"\bblockers?\b[^.;,]{0,40}?\b(?:cleared|resolved|lifted|removed)\b"
    r"|\b(?:cleared|resolved|lifted|removed)\b[^.;,]{0,20}?\bblockers?\b"
    r"|\bunblocked\b|\b(?:\d+|one|two|three|a|an)\s+blockers?\b",
    re.IGNORECASE,
)
# A 'no blockers' that speaks of now, true when none is open.
_BLOCKERS_NOW = re.compile(
    r"\b(?:open|now|currently|remain(?:s|ing)?|outstanding|left)\b[^.;,]{0,40}?\bblockers?\b"
    r"|\bblockers?\b[^.;,]{0,40}?\b(?:open|now|currently|remain(?:s|ing)?|outstanding|left)\b",
    re.IGNORECASE,
)
_SCHEDULE_CHANGE = re.compile(
    r"\b(?:re-?schedul\w*|postpon\w*|deferr?(?:ed|al|ing|s)?|cancel+(?:ed|ing|ation|s)?"
    r"|re-?plann?\w*)\b",
    re.IGNORECASE,
)
_SLIP = re.compile(
    r"\b(?:delay\w*|slipp?(?:ed|ing|s|age)?|pushed\s+back|behind\s+schedule)\b", re.IGNORECASE
)
_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        (
            "zero one two three four five six seven eight nine ten eleven twelve thirteen "
            "fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        ).split()
    )
}
_COUNT_CLAIM = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(green|amber|red|unknown|confirmed|partial"
    r"|stale|missing)\b",
    re.IGNORECASE,
)
_TITLE_WORD = re.compile(r"[a-z0-9]+")
_TITLE_STOPWORDS = frozenset({"with", "from", "into", "that", "this", "when", "then", "than"})


@dataclass(frozen=True, kw_only=True)
class GroundedBrief:
    body: str
    #: Why each dropped or replaced sentence went, for tests and traces.
    dropped: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def ground_brief(text: str, facts: BriefFacts) -> GroundedBrief:
    """Keep the sentences the facts support; correct or drop the rest."""
    people = _PeopleCheck(facts)
    kept: list[str] = []
    seen: set[str] = set()
    dropped: list[str] = []
    replaced: list[str] = []

    def emit(sentence: str) -> None:
        if sentence and sentence not in seen:
            seen.add(sentence)
            kept.append(sentence)

    for sentence in _sentences(text):
        reason = _drop_reason(sentence, facts, people)
        if reason is not None:
            dropped.append(f"{reason}: {sentence}")
            continue
        corrections = _corrections(sentence, facts)
        if corrections:
            replaced.append(sentence)
            for correction in corrections:
                emit(correction)
            continue
        emit(sentence)

    if (facts.blockers_reported or facts.blockers_open) and not any(
        "blocker" in sentence.lower() for sentence in kept
    ):
        emit(facts.blocker_sentence)
    return GroundedBrief(body=" ".join(kept), dropped=tuple(dropped), replaced=tuple(replaced))


def _sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for part in _SENTENCE_BREAK.split(text.strip()):
        cleaned = _BULLET.sub("", part).strip()
        if not cleaned:
            continue
        if cleaned[-1] not in ".!?":
            cleaned += "."
        sentences.append(cleaned)
    return sentences


def _drop_reason(sentence: str, facts: BriefFacts, people: _PeopleCheck) -> str | None:
    for key in SENTENCE_ISSUE_KEY.findall(sentence):
        # "SHA-256" is no issue; "CHK-99" is one the facts do not name.
        if key not in facts.issue_keys and key.rsplit("-", 1)[0] in facts.known_key_prefixes:
            return f"names {key}, which the brief's facts do not"
    person = people.foreign(sentence)
    if person is not None:
        return f"names {person}, whom the brief's facts do not"
    context = facts.context.lower()
    for match in _SCHEDULE_CHANGE.finditer(sentence):
        if match.group(0).lower() not in context:
            return f"says '{match.group(0)}', which no fact says"
    if _SLIP.search(sentence) and not facts.eta_slips:
        return "speaks of a delay, but no ETA moved later"
    return None


def _corrections(sentence: str, facts: BriefFacts) -> list[str]:
    corrections: list[str] = []
    not_done = _claims_open_issue_done(sentence, facts)
    if not_done:
        corrections.extend(issue_sentence(issue) for issue in not_done)
    if _denies_blockers(sentence, facts):
        corrections.append(facts.blocker_sentence)
    if _miscounts(sentence, facts):
        corrections.insert(0, facts.status.sentence)
    return corrections


def _claims_open_issue_done(sentence: str, facts: BriefFacts) -> list[IssueFact]:
    """The issues a sentence names when it calls something done and one is not.

    Every issue the sentence names is restated, the done ones too, so a list
    like 'completion of CHK-8, INS-5 and CHK-17' keeps the true part.
    """
    if not _completion_claimed(sentence):
        return []
    named: dict[str, IssueFact] = {}
    for key in SENTENCE_ISSUE_KEY.findall(sentence):
        issue = facts.issues.get(key)
        if issue is not None:
            named.setdefault(key, issue)
    by_title = {
        issue.key
        for issue in facts.issues.values()
        if not issue.done and issue.key not in named and _names_by_title(sentence, issue)
    }
    named.update((key, facts.issues[key]) for key in sorted(by_title))
    unqualified = [
        issue
        for issue in named.values()
        if not issue.done
        and not (
            _STILL_OPEN.search(sentence)
            if issue.key in by_title
            else _said_open(sentence, issue.key)
        )
    ]
    return list(named.values()) if unqualified else []


def _said_open(sentence: str, key: str) -> bool:
    """Whether the words after an issue's key say it is still open, before another key.

    'CHK-8 was completed, while CHK-17 was merged but remains open' says what
    the tracker says of both.
    """
    for match in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", sentence):
        after = sentence[match.end() : match.end() + _OPEN_REACH]
        if (other := SENTENCE_ISSUE_KEY.search(after)) is not None:
            after = after[: other.start()]
        if _STILL_OPEN.search(after):
            return True
    return False


def _completion_claimed(sentence: str) -> bool:
    return any(
        not _NEGATED_BEFORE.search(sentence[: match.start()])
        for match in _COMPLETION.finditer(sentence)
    )


def _names_by_title(sentence: str, issue: IssueFact) -> bool:
    """Whether a sentence names an issue by most of its title, without its key."""
    words = {
        word
        for word in _TITLE_WORD.findall(issue.title.lower())
        if len(word) >= 4 and word not in _TITLE_STOPWORDS
    }
    if len(words) < 2:
        return False
    present = words & set(_TITLE_WORD.findall(sentence.lower()))
    return len(present) >= min(3, len(words)) and len(present) / len(words) >= 0.6


def _denies_blockers(sentence: str, facts: BriefFacts) -> bool:
    if not _NO_BLOCKERS.search(sentence):
        return False
    if facts.blockers_open:
        return True
    if not facts.blockers_reported:
        return False
    return not (_BLOCKERS_ACCOUNTED.search(sentence) or _BLOCKERS_NOW.search(sentence))


def _miscounts(sentence: str, facts: BriefFacts) -> bool:
    counts = facts.status.counts
    for number, word in _COUNT_CLAIM.findall(sentence):
        expected = counts.get(word.lower())
        if expected is None:
            continue
        value = int(number) if number.isdigit() else _NUMBER_WORDS[number.lower()]
        if value != expected:
            return True
    return False


class _PeopleCheck:
    """Names of the tenant's people the facts do not name, as a sentence may write them."""

    def __init__(self, facts: BriefFacts) -> None:
        allowed = set(facts.people)
        allowed_first = {_first_name(name) for name in allowed}
        tokens: dict[str, str] = {}
        for name in sorted(facts.known_people - allowed):
            tokens[name] = name
            first = _first_name(name)
            if len(first) >= 3 and first not in allowed_first and first not in allowed:
                tokens.setdefault(first, name)
        self._patterns = [
            (re.compile(rf"(?<![\w]){re.escape(token)}(?![\w])"), name)
            for token, name in sorted(tokens.items(), key=lambda entry: -len(entry[0]))
        ]

    def foreign(self, sentence: str) -> str | None:
        for pattern, name in self._patterns:
            if pattern.search(sentence):
                return name
        return None


def _first_name(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else name
