"""Day reports: a project's state, sent on a schedule to the people who act on it.

An admin defines a report: one project, or one release of it, a local send
time and the weekdays it goes out, and where it goes (a chat channel, people
by direct message, email addresses such as mailing lists, a Teams channel).
Each send is a run, kept with the text that went out and how each destination
fared. Someone may write a note for a day's report, which it opens with.

A scheduled run is claimed before anything is sent, one per report and local
day, so a retried or replayed tick never sends a second copy. A send that
fails is reported on its run, not retried: a mailing list would rather miss
one report than receive two.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.domain.errors import OpenProgramError
from core.domain.report_facts import DayReportFacts
from core.domain.rollup import Rag

#: How long after its send time a scheduled report may still go out, so a
#: worker that was down at 18:00 sends at 18:40 but not at midnight.
SEND_WINDOW = timedelta(hours=3)
MAX_DESTINATIONS = 50
MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 1000


class DestinationKind(StrEnum):
    #: A channel in the chat workspace, by its id.
    CHAT_CHANNEL = "chat_channel"
    #: A member, by direct message in chat.
    PERSON = "person"
    #: An email address: a person or a mailing list.
    EMAIL = "email"
    #: The Teams channel the Teams connection posts to.
    TEAMS = "teams"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class RunStatus(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"


class ReportDefinitionError(OpenProgramError):
    """The report an admin entered cannot be saved."""


@dataclass(frozen=True, kw_only=True)
class ReportDestination:
    kind: DestinationKind
    #: Channel id, member id or email address; empty for Teams.
    target: str = ""


@dataclass(frozen=True, kw_only=True)
class ReportSchedule:
    local_time: time
    timezone: str
    #: 0 is Monday.
    weekdays: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class DayReportDefinition:
    tenant_id: str
    report_id: str
    name: str
    project_id: str
    enabled: bool
    schedule: ReportSchedule
    destinations: tuple[ReportDestination, ...]
    updated_at: datetime
    updated_by: str
    #: Report on one release of the project only.
    release_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class DayReportNote:
    """What someone wants said first in one day's report: the news, or the ask."""

    tenant_id: str
    report_id: str
    report_date: date
    text: str
    author: str
    updated_at: datetime


def validated_note(text: str) -> str:
    clean = "\n".join(" ".join(line.split()) for line in text.strip().splitlines()).strip()
    if len(clean) > MAX_NOTE_LENGTH:
        raise ReportDefinitionError(f"A note is at most {MAX_NOTE_LENGTH} characters.")
    return clean


@dataclass(frozen=True, kw_only=True)
class DeliveryOutcome:
    destination: ReportDestination
    ok: bool
    #: A fixed sentence, never an error's own text.
    detail: str


@dataclass(frozen=True, kw_only=True)
class ReportRun:
    tenant_id: str
    run_id: str
    report_id: str
    report_date: date
    trigger: RunTrigger
    started_at: datetime
    finished_at: datetime | None = None
    title: str = ""
    text: str = ""
    outcomes: tuple[DeliveryOutcome, ...] = ()
    actor: str | None = None

    @property
    def status(self) -> RunStatus:
        if self.finished_at is None:
            return RunStatus.SENDING
        if self.outcomes and all(outcome.ok for outcome in self.outcomes):
            return RunStatus.SENT
        if any(outcome.ok for outcome in self.outcomes):
            return RunStatus.PARTIAL
        return RunStatus.FAILED


def report_timezone(schedule: ReportSchedule) -> ZoneInfo:
    try:
        return ZoneInfo(schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def due_date(schedule: ReportSchedule, now: datetime) -> date | None:
    """The local day a scheduled report is due for at ``now``, or None.

    Due on a scheduled weekday from its send time until ``SEND_WINDOW`` after.
    Whether that day's report already went is the run store's to say.
    """
    local = now.astimezone(report_timezone(schedule))
    if local.weekday() not in schedule.weekdays:
        return None
    send_at = datetime.combine(local.date(), schedule.local_time, tzinfo=local.tzinfo)
    if send_at <= local < send_at + SEND_WINDOW:
        return local.date()
    return None


def validated_definition(definition: DayReportDefinition) -> DayReportDefinition:
    """The definition tidied: trimmed, de-duplicated destinations; or ReportDefinitionError."""
    problems: list[str] = []
    name = " ".join(definition.name.split())
    if not name:
        problems.append("a report needs a name")
    elif len(name) > MAX_NAME_LENGTH:
        problems.append(f"the name is longer than {MAX_NAME_LENGTH} characters")
    if not definition.project_id.strip():
        problems.append("a report needs a project")
    weekdays = tuple(sorted(set(definition.schedule.weekdays)))
    if not weekdays:
        problems.append("pick at least one weekday")
    elif any(day < 0 or day > 6 for day in weekdays):
        problems.append("weekdays run from 0 (Monday) to 6 (Sunday)")
    try:
        ZoneInfo(definition.schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"{definition.schedule.timezone!r} is not a timezone")
    destinations = _destinations(definition.destinations, problems)
    if definition.enabled and not destinations:
        problems.append("a report that is on needs somewhere to go")
    if problems:
        text = "; ".join(problems)
        raise ReportDefinitionError(text[0].upper() + text[1:] + ".")
    return DayReportDefinition(
        tenant_id=definition.tenant_id,
        report_id=definition.report_id,
        name=name,
        project_id=definition.project_id.strip(),
        enabled=definition.enabled,
        schedule=ReportSchedule(
            local_time=definition.schedule.local_time.replace(second=0, microsecond=0),
            timezone=definition.schedule.timezone,
            weekdays=weekdays,
        ),
        destinations=destinations,
        updated_at=definition.updated_at,
        updated_by=definition.updated_by,
        release_id=(definition.release_id or "").strip() or None,
    )


def _destinations(
    destinations: Sequence[ReportDestination], problems: list[str]
) -> tuple[ReportDestination, ...]:
    kept: list[ReportDestination] = []
    seen: set[tuple[DestinationKind, str]] = set()
    for destination in destinations:
        target = destination.target.strip()
        if destination.kind is DestinationKind.EMAIL:
            target = target.lower()
            local, _, domain = target.partition("@")
            if not local or "." not in domain or " " in target:
                problems.append(f"{destination.target!r} is not an email address")
                continue
        elif destination.kind is DestinationKind.TEAMS:
            target = ""
        elif not target:
            problems.append(f"a {destination.kind.value.replace('_', ' ')} needs a target")
            continue
        key = (destination.kind, target)
        if key in seen:
            continue
        seen.add(key)
        kept.append(ReportDestination(kind=destination.kind, target=target))
    if len(kept) > MAX_DESTINATIONS:
        problems.append(f"a report goes to at most {MAX_DESTINATIONS} places")
    return tuple(kept)


# --- What a report says --------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ReportGroup:
    """Lines under a heading of their own, such as everything one person is asked for."""

    heading: str
    lines: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ReportTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, kw_only=True)
class ReportSection:
    title: str
    lines: tuple[str, ...] = ()
    groups: tuple[ReportGroup, ...] = ()
    table: ReportTable | None = None
    #: Said in place of the content when there is none.
    empty_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.lines or self.groups or (self.table is not None and self.table.rows))


def day_report_path(project_id: str, report_id: str | None = None) -> str:
    """Where the console shows a report, as a path from the console's own address.

    The report's own page, ``/reports/<project>/daily?report=<id>``: every role
    may open it, which the project's Delivery page is not (it is closed to
    developers, scrum masters and product owners). A link that goes out in a
    message must be a page whoever receives it can open.
    """
    path = f"/reports/{quote(project_id, safe='')}/daily"
    return f"{path}?report={quote(report_id, safe='')}" if report_id else path


@dataclass(frozen=True, kw_only=True)
class DayReport:
    title: str
    project_name: str
    report_date: date
    rag: Rag
    headline: str
    percent_complete: float | None
    progress_line: str
    sections: tuple[ReportSection, ...] = ()
    #: The report's page in the console, relative to the console's address (see
    #: ``day_report_path``). The console links it in place; never as an absolute URL.
    console_path: str | None = None
    #: That page as an address for a message: the console's public address and
    #: ``console_path``, only where the address is set and its readers can open it.
    #: None sends no link rather than one that does not work.
    console_url: str | None = None
    #: Lines a renderer may emphasise, such as items that need action.
    attention_count: int = 0
    footer: str = field(default="Sent by OpenProgram.")
    #: The same report as structured facts, for the console to draw. No renderer
    #: reads it: what is sent is the sections above, unchanged by it.
    facts: DayReportFacts | None = None


RAG_WORDS = {
    Rag.GREEN: "On track",
    Rag.AMBER: "At risk",
    Rag.RED: "Off track",
    Rag.UNKNOWN: "Status unknown",
}

_BAR_WIDTH = 20


def progress_bar(percent: float | None, width: int = _BAR_WIDTH) -> str:
    """A text bar ("█████░░░░░") that reads the same in chat, email and Teams."""
    if percent is None:
        return "░" * width
    filled = round(max(0.0, min(100.0, percent)) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render_text(report: DayReport) -> str:
    """The report as plain text, as a direct message or an email's text part carries it."""
    lines = [
        report.title,
        f"{RAG_WORDS[report.rag]}: {report.headline}",
        "",
        f"{progress_bar(report.percent_complete)} {report.progress_line}",
    ]
    for section in report.sections:
        lines.extend(["", section.title.upper()])
        if section.is_empty:
            if section.empty_text:
                lines.append(section.empty_text)
            continue
        lines.extend(f"• {line}" for line in section.lines)
        for group in section.groups:
            lines.append(group.heading)
            lines.extend(f"  • {line}" for line in group.lines)
        if section.table is not None:
            lines.extend(table_lines(section.table))
    if report.console_url:
        lines.extend(["", f"Open in OpenProgram: {report.console_url}"])
    lines.extend(["", report.footer])
    return "\n".join(lines)


def table_lines(table: ReportTable) -> list[str]:
    """A table as lines, each row's cells named by their column, for formats without tables."""
    return [
        "• "
        + " · ".join(
            f"{column}: {cell}" if index else cell
            for index, (column, cell) in enumerate(zip(table.columns, row, strict=False))
            if cell
        )
        for row in table.rows
    ]
