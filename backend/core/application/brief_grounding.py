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

Before those, each sentence is checked clause by clause for whom it says did
what (``brief_attribution``, N51-N53): a blocker, a merge or an ETA change
said to be someone's that the facts give to someone else, or to nobody, is
dropped from its sentence or corrected, and the rest of the sentence stays.

When blockers were reported or are open and no sentence left mentions them,
the blocker sentence is added. No model call, no I/O.

``compose_brief`` reads the model's structured answer -- a one-line verdict
and three or four short bullets -- validates it, grounds each part the same
way, and fills or trims it to shape from sentences built from the facts. A
structured brief speaks plainly: a sentence counting statuses by colour ("20
amber statuses") says nothing to a director and is dropped, and a merge
request named by its number alone is named in full or dropped. It names no
clock time (the page shows times on its reader's clock), and no bullet says
again what the verdict says.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from core.application.brief_attribution import PersonFinder, check_clause, clauses, rebuild
from core.application.brief_facts import (
    SENTENCE_ISSUE_KEY,
    BriefFacts,
    IssueFact,
    issue_sentence,
)
from core.application.json_parsing import extract_json_object
from core.domain.brief import MAX_BULLETS, MIN_BULLETS, structured_body

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
    grounding = _Grounding(facts)
    kept = grounding.sentences(text)
    if (facts.blockers_reported or facts.blockers_open) and not _mentions_blockers(kept):
        kept.append(facts.blocker_sentence)
    return GroundedBrief(
        body=" ".join(_unique(kept)),
        dropped=tuple(grounding.dropped),
        replaced=tuple(grounding.replaced),
    )


class _Grounding:
    """One brief's checks, with what they dropped and replaced, for tests and traces.

    ``plain`` is a structured brief's: colour counts are dropped as jargon.
    """

    def __init__(self, facts: BriefFacts, *, plain: bool = False) -> None:
        self.facts = facts
        self.plain = plain
        self.people = _PeopleCheck(facts)
        self.finder = PersonFinder(facts)
        self.dropped: list[str] = []
        self.replaced: list[str] = []

    def sentences(self, text: str) -> list[str]:
        """Every sentence of ``text`` the facts support, corrected where they say otherwise."""
        kept: list[str] = []
        for written in _sentences(text):
            sentence = self._attributed(written)
            if not sentence:
                continue
            reason = _drop_reason(sentence, self.facts, self.people)
            if reason is None and self.plain and _COLOUR_COUNT.search(sentence):
                reason = "counts statuses by colour, which tells a reader nothing"
            if reason is not None:
                self.dropped.append(f"{reason}: {sentence}")
                continue
            corrections = _corrections(sentence, self.facts)
            if self.plain:
                corrections = [line for line in corrections if not _COLOUR_COUNT.search(line)]
                if not corrections and _miscounts(sentence, self.facts):
                    self.dropped.append(f"gives a count the facts do not: {sentence}")
                    continue
            if corrections:
                self.replaced.append(sentence)
                kept.extend(corrections)
                continue
            kept.append(sentence)
        return _unique(kept)

    def _attributed(self, sentence: str) -> str:
        """The sentence without the clauses that give someone what was another's (N51-N53)."""
        parts = clauses(sentence)
        kept: list[tuple[str, str]] = []
        instead: list[str] = []
        changed = False
        before = ""
        for clause, separator in parts:
            verdict = check_clause(clause, before, self.facts, self.finder)
            before += clause + separator
            if verdict.reason is not None:
                changed = True
                self.replaced.append(f"{verdict.reason}: {clause.strip()}")
                instead.extend(verdict.instead)
            if verdict.text is not None:
                kept.append((verdict.text, separator))
        if not changed:
            return sentence
        rebuilt = rebuild(kept)
        return " ".join(part for part in (rebuilt, *instead) if part)


def _mentions_blockers(sentences: Sequence[str]) -> bool:
    return any("blocker" in sentence.lower() for sentence in sentences)


def _unique(sentences: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(sentence for sentence in sentences if sentence))


# ---- structured briefs ----------------------------------------------------------

# A verdict is one line of about twenty words; so is a bullet.
_VERDICT_CHARS = 170
_BULLET_CHARS = 190
# A clock time and what introduces it: ", asked 07:46 UTC", " at 4 Oct 06:05 UTC".
_CLOCK = re.compile(
    r"(?:,\s*|\s+)?(?:\b(?:asked|at|as\s+of|since|by|until|from|first\s+at)\s+)?"
    r"(?:\d{1,2}\s+[A-Z][a-z]{2}\s+)?\b\d{1,2}:\d{2}\b"
    r"(?:\s*(?:UTC|GMT|IST|CET|CEST|EET|EEST|BST|EST|EDT|PST|PDT))?"
)
# Words two sentences share when they say the same thing about the day.
_TOPICS = {
    "check-in": re.compile(
        r"check-?ins?\b.*\b(?:answer|repl|respon|confirm|inferr)"
        r"|\b(?:answer|repl|respon|confirm|inferr)\w*\b.*\bcheck-?ins?\b",
        re.IGNORECASE,
    ),
    "blockers": re.compile(r"\bblock", re.IGNORECASE),
    "merges": re.compile(r"\bmerg", re.IGNORECASE),
}
_CONTENT_WORD = re.compile(r"[a-z][a-z'-]{3,}")
_COMMON_WORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "have",
        "been",
        "their",
        "they",
        "them",
        "were",
        "will",
        "into",
        "today",
        "today's",
        "still",
        "need",
        "needs",
        "until",
        "because",
    }
)
# "20 amber statuses", "7 green and 4 unknown status cells": a count by colour.
_COLOUR_COUNT = re.compile(
    r"\b(?:\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(?:green|amber|red|unknown)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, kw_only=True)
class ComposedBrief:
    """A brief's stored body, structured, and what grounding changed on the way."""

    body: str
    verdict: str
    bullets: tuple[str, ...]
    #: Whether the model's answer had the verdict-and-bullets shape.
    structured: bool
    dropped: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def compose_brief(text: str, facts: BriefFacts) -> ComposedBrief:
    """The stored body of a brief from the model's answer: a verdict, then 3-5 bullets.

    The answer is read as JSON ``{"verdict": ..., "bullets": [...]}``. A
    verdict and each bullet are grounded like any sentence; one the facts
    take away is dropped, and the verdict falls back to the status sentence.
    An answer that is prose is grounded sentence by sentence into bullets. The
    bullets are then filled to three, when they run short, from sentences
    built from the facts (the blocker sentence first when blockers were
    reported and no bullet says so), and cut to five.
    """
    grounding = _Grounding(facts, plain=True)
    parsed = _parsed_structure(text)
    if parsed is not None:
        written_verdict, written_bullets = parsed
        verdict = " ".join(grounding.sentences(written_verdict))
        bullets = [
            joined
            for bullet in written_bullets
            if (joined := " ".join(grounding.sentences(bullet)))
        ]
    else:
        verdict = ""
        bullets = grounding.sentences(text)
    verdict = _one_line(without_clock(verdict), _VERDICT_CHARS) or _fallback_verdict(facts)
    shaped = _shaped_bullets(
        [_one_line(without_clock(bullet), _BULLET_CHARS) for bullet in bullets], facts, verdict
    )
    return ComposedBrief(
        body=structured_body(verdict, shaped),
        verdict=verdict,
        bullets=tuple(shaped),
        structured=parsed is not None,
        dropped=tuple(grounding.dropped),
        replaced=tuple(grounding.replaced),
    )


def fallback_brief(facts: BriefFacts) -> ComposedBrief:
    """A structured brief from the facts alone, when the model gave nothing usable."""
    verdict = _fallback_verdict(facts)
    shaped = _shaped_bullets([], facts, verdict)
    return ComposedBrief(
        body=structured_body(verdict, shaped),
        verdict=verdict,
        bullets=tuple(shaped),
        structured=False,
    )


def _parsed_structure(text: str) -> tuple[str, list[str]] | None:
    """The verdict and bullets of a JSON answer; None when it has not that shape."""
    decoded = extract_json_object(text)
    if decoded is None:
        return None
    verdict = decoded.get("verdict")
    bullets = decoded.get("bullets")
    if not isinstance(verdict, str) or not verdict.strip() or not isinstance(bullets, list):
        return None
    written = [_one_line(bullet, _BULLET_CHARS) for bullet in bullets if isinstance(bullet, str)]
    written = [bullet for bullet in written if bullet]
    if not written:
        return None
    return verdict, written[: MAX_BULLETS + 2]


def _fallback_verdict(facts: BriefFacts) -> str:
    """The day's headline, as the hero says it; else the status sentence."""
    return without_clock(facts.verdict_fallback or facts.status.sentence)


def without_clock(text: str) -> str:
    """A sentence with its clock times taken out, and what introduced them.

    "(0 of 10, asked 07:46 UTC)" reads "(0 of 10)"; "cleared at 4 Oct 06:06 UTC"
    reads "cleared". A stored brief is read on every reader's clock; the page
    beside it shows times in the reader's zone (one clock per page).
    """
    cleaned = _CLOCK.sub("", text)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\(\s*[,;]\s*", "(", cleaned)
    cleaned = re.sub(r"\s*[,;]\s*\)", ")", cleaned)
    cleaned = re.sub(r"\s+([,.;:)])", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def repeats(bullet: str, verdict: str, facts: BriefFacts) -> bool:
    """Whether a bullet says again what the verdict says.

    It does when it names no person or ticket the verdict does not, and either
    speaks of the same thing of the day (its check-in, blockers, merges) or
    shares most of its words.
    """
    if _marks(bullet, facts) - _marks(verdict, facts):
        return False
    topics = {name for name, pattern in _TOPICS.items() if pattern.search(bullet)}
    if topics & {name for name, pattern in _TOPICS.items() if pattern.search(verdict)}:
        return True
    words = _content_words(bullet)
    return bool(words) and len(words & _content_words(verdict)) / len(words) >= 0.6


def _content_words(text: str) -> set[str]:
    return {word[:6] for word in _CONTENT_WORD.findall(text.lower()) if word not in _COMMON_WORDS}


def _shaped_bullets(bullets: Sequence[str], facts: BriefFacts, verdict: str) -> list[str]:
    """Three or four bullets: the model's, the open blockers, then who must act.

    A blocker still open is always said; one that cleared is the model's to
    leave out (what matters today). Bullets that ran short are filled only
    from short sentences, true by construction, saying who needs to act.
    """
    shaped = [
        bullet
        for bullet in _unique(bullets)
        if bullet
        and bullet != verdict
        and not _only_no_blockers(bullet, facts)
        and not repeats(bullet, verdict, facts)
    ]
    if facts.blockers_open and not _mentions_blockers([verdict, *shaped]):
        shaped.insert(min(len(shaped), 1), without_clock(facts.blocker_sentence))
    for line in (without_clock(line) for line in facts.action_lines):
        if len(shaped) >= MIN_BULLETS:
            break
        if (
            line not in shaped
            and not repeats(line, verdict, facts)
            and not _says_same(line, shaped, facts)
        ):
            shaped.append(line)
    if not shaped:
        # Nothing to say but what is true of the window: never a bare verdict.
        shaped.append(without_clock(facts.blocker_sentence))
    return shaped[:MAX_BULLETS]


def _only_no_blockers(bullet: str, facts: BriefFacts) -> bool:
    """A bullet that only says no blocker is open, while none is: nothing to act on today."""
    if facts.blockers_open or not _mentions_blockers([bullet]):
        return False
    return not SENTENCE_ISSUE_KEY.search(bullet) and not _marks(bullet, facts)


def _says_same(line: str, written: Sequence[str], facts: BriefFacts) -> bool:
    """Whether a fact line repeats a written part: the same people and keys, or topic."""
    marks = _marks(line, facts)
    if not marks:
        return "check-in" in line and any("check-in" in part for part in written)
    return any(marks <= _marks(part, facts) for part in written)


def _marks(text: str, facts: BriefFacts) -> set[str]:
    return set(SENTENCE_ISSUE_KEY.findall(text)) | {
        name for name in facts.known_people if name in text
    }


def _one_line(text: str, limit: int) -> str:
    """Text on one line; past ``limit`` only its first sentence, closed."""
    flat = " ".join(text.split())
    flat = _BULLET.sub("", flat).strip()
    if len(flat) > limit:
        first = _SENTENCE_BREAK.split(flat, maxsplit=1)[0]
        flat = first if len(first) <= limit else first[: limit - 1].rsplit(" ", 1)[0] + "…"
    if flat and flat[-1] not in ".!?…":
        flat += "."
    return flat


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
