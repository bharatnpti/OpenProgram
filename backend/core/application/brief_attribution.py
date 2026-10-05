"""Who a brief's clause says did something, checked against who the facts say did (N51-N53).

``brief_grounding`` checks what a sentence claims; this checks whom it claims
it of, clause by clause:

- A blocker said to be someone's ("his blocker was cleared", "Ben is blocked")
  when no check-in of theirs reported one and none of theirs is open (N51:
  the cleared blocker was the person waiting on them).
- A merge said to be someone's ("merged by Ada", "Ada merged SHOP-2") when the
  merge commit's author was someone else, or nobody knows who merged it (N52:
  the request's opener written as its merger).
- An ETA change said to be someone's when no check-in of theirs reported one,
  an ETA change with no one named when nobody reported any, or an earlier
  change tied to the person's latest check-in (N53).

A pronoun stands for the last person the sentence named before it. A clause
names a person by their full name, or by a first name no one else shares.
Deterministic text work: no model call, no I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from core.application.brief_facts import (
    BARE_MERGE_REQUEST,
    SENTENCE_ISSUE_KEY,
    BriefFacts,
    MergeFact,
    resolve_bare_merge_request,
)

# Where a sentence splits into clauses that each say one thing: "X; his blocker
# was cleared, but Y" is three. The joining word starts the next clause.
_CLAUSE_BREAK = re.compile(
    r"\s*;\s*|,\s+(?=(?:but|while|whereas|although|though|and|so|yet)\b)|\s+(?=but\b)"
)
_LEADING_JOIN = re.compile(r"^(?:and|but|while|whereas|although|though|so|yet)\s+", re.IGNORECASE)
_PRONOUN = re.compile(r"\b(?:he|him|his|she|her|hers|they|them|their|theirs)\b", re.IGNORECASE)

_BLOCKER_WORD = re.compile(r"\b(?:blockers?|blocked|blocking|unblock(?:ed|ing)?)\b", re.IGNORECASE)
_NO_BLOCKER = re.compile(
    r"\b(?:no|zero|without|not|nobody|none)\b[^.;,]{0,30}?\bblock", re.IGNORECASE
)

# A word that says an ETA moved, beside the ETA it moved.
_ETA = r"\b(?:etas?|deadlines?|due dates?)\b"
_MOVED = (
    r"\b(?:mov(?:e|ed|es|ing)|slip\w*|push\w*|exten\w*|increas\w*|updat\w*|chang\w*"
    r"|revis\w*|adjust\w*|delay\w*|later)\b"
)
_ETA_CHANGE = re.compile(
    rf"{_ETA}[^.;]{{0,40}}?{_MOVED}|{_MOVED}[^.;]{{0,25}}?{_ETA}|{_ETA}[^.;]{{0,12}}?[+]\d+",
    re.IGNORECASE,
)
_NO_ETA_CHANGE = re.compile(
    rf"\b(?:no|without|none)\b[^.;]{{0,25}}?{_ETA}|{_ETA}[^.;]{{0,25}}?"
    r"\b(?:unchanged|held|holds?|stay(?:s|ed)?|same|agree|overlap)\b",
    re.IGNORECASE,
)
_LATEST_CHECKIN = re.compile(
    r"\b(?:partial|latest|last|most\s+recent|today'?s|new|newest)\s+check-?ins?\b", re.IGNORECASE
)
_MERGED_AFTER_NAME = re.compile(r"^(?:'s)?\s+(?:has\s+|had\s+|have\s+|then\s+)?merged\b")
_MERGED_BY_BEFORE = re.compile(r"\bmerged\s+(?:it\s+|them\s+)?by\s+(?:[\w-]+\s+){0,1}$")


@dataclass(frozen=True, kw_only=True)
class ClauseVerdict:
    """What became of one clause: kept, corrected, or dropped (``text`` None)."""

    text: str | None
    #: Why it changed, for tests and traces; None when it is kept as written.
    reason: str | None = None
    #: True sentences to say instead, built from the facts only.
    instead: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class _Mention:
    start: int
    end: int
    name: str


class PersonFinder:
    """The tenant's people as a sentence may write them: full names, unshared first names."""

    def __init__(self, facts: BriefFacts) -> None:
        names = sorted(facts.known_people, key=lambda name: (-len(name), name))
        firsts = Counter(name.split()[0] for name in names if name.split())
        patterns: list[tuple[re.Pattern[str], str]] = []
        for name in names:
            patterns.append((re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])"), name))
            first = name.split()[0] if name.split() else name
            if first != name and len(first) >= 3 and firsts[first] == 1:
                patterns.append((re.compile(rf"(?<![\w]){re.escape(first)}(?![\w])"), name))
        self._patterns = patterns

    def mentions(self, text: str) -> list[_Mention]:
        found = sorted(
            (
                _Mention(start=match.start(), end=match.end(), name=name)
                for pattern, name in self._patterns
                for match in pattern.finditer(text)
            ),
            key=lambda mention: (mention.start, -(mention.end - mention.start)),
        )
        kept: list[_Mention] = []
        for mention in found:
            if kept and mention.start < kept[-1].end:
                continue
            kept.append(mention)
        return kept

    def subjects(self, clause: str, before: str) -> set[str]:
        """Whom a clause speaks of: the people it names, else whom its pronoun stands for."""
        named = {mention.name for mention in self.mentions(clause)}
        if named or not _PRONOUN.search(clause):
            return named
        earlier = self.mentions(before)
        return {earlier[-1].name} if earlier else set()


def clauses(sentence: str) -> list[tuple[str, str]]:
    """A sentence as (clause, the separator after it) pairs; the last separator is ""."""
    parts: list[tuple[str, str]] = []
    last = 0
    for match in _CLAUSE_BREAK.finditer(sentence):
        if match.start() > last:
            parts.append((sentence[last : match.start()], match.group(0)))
            last = match.end()
    parts.append((sentence[last:], ""))
    return parts


def rebuild(parts: list[tuple[str, str]]) -> str:
    """Kept clauses as a sentence again, capitalised and closed; "" when none is left."""
    text = ""
    for index, (clause, separator) in enumerate(parts):
        piece = clause.strip()
        if not piece:
            continue
        if not text or text.rstrip().endswith(";"):
            piece = _LEADING_JOIN.sub("", piece)
        text += piece + (separator if index < len(parts) - 1 else "")
    text = text.strip().rstrip(",;").strip()
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return f"{text[:1].upper()}{text[1:]}"


def check_clause(
    clause: str, before: str, facts: BriefFacts, finder: PersonFinder
) -> ClauseVerdict:
    """Keep, correct or drop one clause by whom it says did what."""
    subjects = finder.subjects(clause, before)
    if (reason := _blocker_misattributed(clause, subjects, facts)) is not None:
        return ClauseVerdict(text=None, reason=reason)
    if (reason := _eta_misattributed(clause, subjects, facts)) is not None:
        return ClauseVerdict(text=None, reason=reason)
    named = _merge_request_named(clause, subjects | finder.subjects(before, ""), facts)
    if named.text is None:
        return named
    merged = _merge_checked(named.text, facts, finder)
    if merged.reason is None and named.reason is not None:
        return named
    return merged


def _merge_request_named(clause: str, people: set[str], facts: BriefFacts) -> ClauseVerdict:
    """A merge request named by its number alone, named in full: "insights-pipeline !1 (INS-2)".

    A reader cannot tell which repository's "!1" is. When the clause's people
    or the window's merge requests tell it, it is named in full; else the
    clause goes.
    """
    match = BARE_MERGE_REQUEST.search(clause)
    if match is None or not facts.merges:
        return ClauseVerdict(text=clause)
    before = clause[: match.start()].rstrip().rsplit(" ", 1)[-1].casefold()
    if before and any(before == merge.repo for merge in facts.merges):
        return ClauseVerdict(text=clause)
    meant = resolve_bare_merge_request(clause, people, facts.merges)
    if meant is None:
        return ClauseVerdict(
            text=None, reason=f"names merge request {match.group(0).strip()} by number alone"
        )
    if meant.named in clause or meant.label in clause:
        return ClauseVerdict(text=clause)
    corrected = clause[: match.start()] + meant.named + clause[match.end() :]
    return ClauseVerdict(
        text=corrected, reason=f"names {match.group(0).strip()} in full as {meant.named}"
    )


def _blocker_misattributed(clause: str, subjects: set[str], facts: BriefFacts) -> str | None:
    if not subjects or not _BLOCKER_WORD.search(clause) or _NO_BLOCKER.search(clause):
        return None
    if subjects & facts.blocker_people:
        return None
    # A ticket the tracker itself shows blocked may be called blocked.
    for key in SENTENCE_ISSUE_KEY.findall(clause):
        issue = facts.issues.get(key)
        if issue is not None and "block" in issue.tracker_label.lower():
            return None
    return f"gives {_names(subjects)} a blocker no check-in of theirs reported"


def _eta_misattributed(clause: str, subjects: set[str], facts: BriefFacts) -> str | None:
    if not _ETA_CHANGE.search(clause) or _NO_ETA_CHANGE.search(clause):
        return None
    changed = set(facts.eta_changes)
    if not subjects:
        return None if changed else "speaks of an ETA change no check-in reported"
    moved = subjects & changed
    if not moved:
        return f"gives {_names(subjects)} an ETA change no check-in of theirs reported"
    if _LATEST_CHECKIN.search(clause) and not any(
        change.latest for person in moved for change in facts.eta_changes[person]
    ):
        return "ties an earlier ETA change to the latest check-in"
    return None


def _merge_checked(clause: str, facts: BriefFacts, finder: PersonFinder) -> ClauseVerdict:
    """A clause saying who merged a merge request, made true or dropped (N52)."""
    for mention in finder.mentions(clause):
        merged_by = _MERGED_BY_BEFORE.search(clause[: mention.start])
        merged_after = _MERGED_AFTER_NAME.match(clause[mention.end :])
        if not (merged_by or merged_after):
            continue
        merges = _merges_named(clause, facts) or [merge for merge in facts.merges if merge.merged]
        mergers = {merge.merger for merge in merges if merge.merger}
        if mention.name in mergers:
            continue
        if len(mergers) == 1:
            # "merged by Ben", "Ben merged SHOP-2": the one merger takes his place.
            (right,) = mergers
            corrected = clause[: mention.start] + right + clause[mention.end :]
            return ClauseVerdict(
                text=corrected, reason=f"says {mention.name} merged it; {right} did"
            )
        instead = tuple(
            f"{_merge_label(merge)} was merged"
            + (f" by {merge.merger}" if merge.merger else "")
            + "."
            for merge in merges
            if merge.merged and (merge.keys & set(SENTENCE_ISSUE_KEY.findall(clause)))
        )
        return ClauseVerdict(
            text=None,
            reason=f"says {mention.name} merged it; no fact says so",
            instead=instead,
        )
    return ClauseVerdict(text=clause)


def _merges_named(clause: str, facts: BriefFacts) -> list[MergeFact]:
    """The merge requests a clause names: by an issue key, or by their number."""
    keys = set(SENTENCE_ISSUE_KEY.findall(clause))
    named = [merge for merge in facts.merges if merge.keys & keys]
    for merge in facts.merges:
        number = re.escape(merge.number)
        if re.search(rf"(?:[!#]|merge request\s+|pull request\s+){number}\b", clause) and (
            merge not in named
        ):
            named.append(merge)
    return named


def _merge_label(merge: MergeFact) -> str:
    keys = sorted(merge.keys)
    return f"{keys[0]} ({merge.label})" if keys else merge.label.capitalize()


def _names(people: set[str]) -> str:
    ordered = sorted(people)
    if len(ordered) == 1:
        return ordered[0]
    return ", ".join(ordered[:-1]) + " and " + ordered[-1]
