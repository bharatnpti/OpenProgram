"""A day report in each channel's own format: Slack mrkdwn, an HTML email, a Teams card."""

from __future__ import annotations

from html import escape

from core.domain.reports import RAG_WORDS, DayReport, ReportSection, progress_bar, table_lines
from core.domain.rollup import Rag
from infra.adapters.reports.teams import teams_card_payload

_RAG_COLOURS = {
    Rag.GREEN: "#00a86b",
    Rag.AMBER: "#f2a900",
    Rag.RED: "#d90000",
    Rag.UNKNOWN: "#6b6b6b",
}


def slack_text(report: DayReport) -> str:
    """mrkdwn for chat.postMessage. Text from the report is escaped, never read as markup."""
    lines = [
        f"*{_slack(report.title)}*",
        f"*{RAG_WORDS[report.rag]}:* {_slack(report.headline)}",
        f"`{progress_bar(report.percent_complete)}` {_slack(report.progress_line)}",
    ]
    for section in report.sections:
        lines.append("")
        lines.append(f"*{_slack(section.title)}*")
        if section.is_empty:
            if section.empty_text:
                lines.append(f"_{_slack(section.empty_text)}_")
            continue
        lines.extend(f"• {_slack(line)}" for line in section.lines)
        for group in section.groups:
            lines.append(f"_{_slack(group.heading)}_")
            lines.extend(f"    • {_slack(line)}" for line in group.lines)
        if section.table is not None:
            lines.extend(_slack(line) for line in table_lines(section.table))
    if report.console_url:
        lines.extend(["", f"<{_slack(report.console_url)}|Open in OpenProgram>"])
    return "\n".join(lines)


def email_html(report: DayReport) -> str:
    colour = _RAG_COLOURS[report.rag]
    percent = max(0.0, min(100.0, report.percent_complete or 0.0))
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1a1a1a;'
        'max-width:640px">',
        f'<h2 style="margin:0 0 4px">{escape(report.title)}</h2>',
        f'<p style="margin:0 0 12px"><strong style="color:{colour}">'
        f"{escape(RAG_WORDS[report.rag])}</strong>: {escape(report.headline)}</p>",
        '<div style="background:#e6e6e6;border-radius:6px;height:12px;width:100%">'
        f'<div style="background:#e20074;border-radius:6px;height:12px;width:{percent:.0f}%">'
        "</div></div>",
        f'<p style="margin:6px 0 16px">{escape(report.progress_line)}</p>',
    ]
    for section in report.sections:
        parts.append(f'<h3 style="margin:16px 0 6px;font-size:15px">{escape(section.title)}</h3>')
        parts.extend(_html_section(section))
    if report.console_url:
        parts.append(
            f'<p style="margin:20px 0 0"><a href="{escape(report.console_url, quote=True)}">'
            "Open in OpenProgram</a></p>"
        )
    parts.append(
        f'<p style="margin:20px 0 0;color:#6b6b6b;font-size:12px">{escape(report.footer)}</p>'
    )
    parts.append("</div>")
    return "".join(parts)


def _html_section(section: ReportSection) -> list[str]:
    if section.is_empty:
        return (
            [f'<p style="margin:0;color:#6b6b6b">{escape(section.empty_text)}</p>']
            if section.empty_text
            else []
        )
    parts: list[str] = []
    if section.lines:
        parts.append(_html_list(section.lines))
    for group in section.groups:
        parts.append(f'<p style="margin:10px 0 2px;font-weight:bold">{escape(group.heading)}</p>')
        parts.append(_html_list(group.lines))
    if section.table is not None and section.table.rows:
        cell = 'style="border-bottom:1px solid #e6e6e6;padding:4px 8px;text-align:left"'
        parts.append('<table style="border-collapse:collapse;font-size:13px;width:100%">')
        parts.append(
            "<tr>"
            + "".join(f"<th {cell}>{escape(column)}</th>" for column in section.table.columns)
            + "</tr>"
        )
        parts.extend(
            "<tr>" + "".join(f"<td {cell}>{escape(value)}</td>" for value in row) + "</tr>"
            for row in section.table.rows
        )
        parts.append("</table>")
    return parts


def _html_list(lines: tuple[str, ...]) -> str:
    items = "".join(f'<li style="margin:2px 0">{escape(line)}</li>' for line in lines)
    return f'<ul style="margin:0;padding-left:20px">{items}</ul>'


def teams_payload(report: DayReport) -> dict[str, object]:
    lines = [
        f"**{RAG_WORDS[report.rag]}:** {report.headline}",
        f"`{progress_bar(report.percent_complete)}` {report.progress_line}",
    ]
    for section in report.sections:
        lines.append(f"**{section.title}**")
        if section.is_empty:
            if section.empty_text:
                lines.append(f"_{section.empty_text}_")
            continue
        if section.lines:
            lines.append("\n".join(f"- {line}" for line in section.lines))
        for group in section.groups:
            lines.append(f"_{group.heading}_\n" + "\n".join(f"- {line}" for line in group.lines))
        if section.table is not None:
            lines.append("\n".join(table_lines(section.table)))
    link = ("Open in OpenProgram", report.console_url) if report.console_url else None
    return teams_card_payload(report.title, lines, link=link)


def _slack(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
