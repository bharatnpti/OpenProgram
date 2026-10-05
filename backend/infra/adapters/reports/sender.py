"""Send a day report to one destination, through the tenant's connections.

Every outcome is a fixed sentence: an error's own text can carry a token, an
address or an internal host's error page, so it never reaches the run record.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx
import structlog

from core.domain.errors import ProviderConfigurationError, ProviderUnavailable
from core.domain.messaging import ChatUserRef, OutboundMessage
from core.domain.reports import (
    DayReport,
    DeliveryOutcome,
    DestinationKind,
    ReportDestination,
    render_text,
)
from core.ports.chat import ChatProvider
from core.ports.connections import ConnectionResolver
from infra.adapters.connections.specs import EMAIL, SMTP_PLAIN, SMTP_SSL, TEAMS
from infra.adapters.reports.render import email_html, slack_text, teams_payload

_TIMEOUT_SECONDS = 15.0
_logger = structlog.get_logger(__name__)

type ChannelPoster = Callable[[str, str], Awaitable[str]]
type SmtpSend = Callable[[Mapping[str, str], EmailMessage], None]


@dataclass(frozen=True)
class ConnectionReportSender:
    connections: ConnectionResolver | None
    chat_provider: Callable[[], ChatProvider]
    #: Post mrkdwn to a channel, or None when the chat provider has no channels.
    channel_poster: ChannelPoster | None
    member_chat_id: Callable[[str, str], Awaitable[str | None]]
    smtp_send: SmtpSend | None = None
    transport: httpx.AsyncBaseTransport | None = None

    async def deliver(
        self, tenant_id: str, destination: ReportDestination, report: DayReport
    ) -> DeliveryOutcome:
        try:
            if destination.kind is DestinationKind.CHAT_CHANNEL:
                detail = await self._chat_channel(destination.target, report)
            elif destination.kind is DestinationKind.PERSON:
                detail = await self._person(tenant_id, destination.target, report)
            elif destination.kind is DestinationKind.EMAIL:
                detail = await self._email(tenant_id, destination.target, report)
            else:
                detail = await self._teams(tenant_id, report)
        except _NotSent as failed:
            return DeliveryOutcome(destination=destination, ok=False, detail=failed.detail)
        except Exception as error:
            _logger.warning(
                "day_report_delivery_failed",
                tenant_id=tenant_id,
                kind=destination.kind.value,
                error_type=type(error).__name__,
            )
            return DeliveryOutcome(
                destination=destination, ok=False, detail="Not sent: an unexpected error."
            )
        return DeliveryOutcome(destination=destination, ok=True, detail=detail)

    async def _chat_channel(self, channel_id: str, report: DayReport) -> str:
        if self.channel_poster is None:
            raise _NotSent("Not sent: chat channels need the Slack chat provider.")
        try:
            await self.channel_poster(channel_id, slack_text(report))
        except ProviderConfigurationError as error:
            raise _NotSent("Not sent: Slack refused the bot token or it is not set.") from error
        except ProviderUnavailable as error:
            reason = str(error)
            if "not_in_channel" in reason or "channel_not_found" in reason:
                raise _NotSent(
                    "Not sent: the OpenProgram app is not in that channel. Invite it first."
                ) from error
            raise _NotSent("Not sent: Slack did not accept the message.") from error
        return "Posted to the channel."

    async def _person(self, tenant_id: str, member_id: str, report: DayReport) -> str:
        chat_id = await self.member_chat_id(tenant_id, member_id)
        if chat_id is None:
            raise _NotSent("Not sent: that member is not in the directory any more.")
        try:
            await self.chat_provider().send_dm(
                ChatUserRef(tenant_id=tenant_id, external_id=chat_id),
                OutboundMessage(
                    tenant_id=tenant_id,
                    text=render_text(report),
                    correlation_id=f"day-report-{member_id}-{report.report_date.isoformat()}",
                    metadata={"kind": "day_report"},
                ),
            )
        except ProviderUnavailable as error:
            raise _NotSent("Not sent: the chat provider did not take the message.") from error
        return "Sent as a direct message."

    async def _email(self, tenant_id: str, address: str, report: DayReport) -> str:
        values = await self._connection(tenant_id, EMAIL)
        if values is None:
            raise _NotSent("Not sent: email is not set up. Add it under Admin, Integrations.")
        message = EmailMessage()
        message["Subject"] = report.title
        message["From"] = formataddr(
            (values.get("from_name") or "OpenProgram", values.get("from_address") or "")
        )
        message["To"] = address
        message["Message-ID"] = make_msgid(
            domain=(values.get("from_address") or "x@openprogram").split("@")[-1]
        )
        message.set_content(render_text(report))
        message.add_alternative(email_html(report), subtype="html")
        send = self.smtp_send or _smtp_send
        try:
            await asyncio.wait_for(asyncio.to_thread(send, values, message), _TIMEOUT_SECONDS + 10)
        except smtplib.SMTPRecipientsRefused as error:
            raise _NotSent("Not sent: the mail server refused that address.") from error
        except smtplib.SMTPAuthenticationError as error:
            raise _NotSent(
                "Not sent: the mail server refused the user name or password."
            ) from error
        except (smtplib.SMTPException, OSError, TimeoutError) as error:
            raise _NotSent("Not sent: the mail server could not be reached.") from error
        return "Emailed."

    async def _teams(self, tenant_id: str, report: DayReport) -> str:
        values = await self._connection(tenant_id, TEAMS)
        url = values.get("webhook_url") if values is not None else None
        if not url:
            raise _NotSent("Not sent: Teams is not set up. Add it under Admin, Integrations.")
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=False, transport=self.transport
        ) as client:
            try:
                response = await client.post(url, json=teams_payload(report))
            except httpx.HTTPError as error:
                raise _NotSent("Not sent: the Teams webhook could not be reached.") from error
        if response.status_code >= 400:
            raise _NotSent("Not sent: Teams refused the webhook. Copy its URL again.")
        return "Posted to the Teams channel."

    async def _connection(self, tenant_id: str, connector: str) -> Mapping[str, str] | None:
        if self.connections is None:
            return None
        resolved = await self.connections.resolve(tenant_id, connector)
        return resolved.values if resolved is not None else None


class _NotSent(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _smtp_send(values: Mapping[str, str], message: EmailMessage) -> None:
    host = values.get("host", "")
    port = int(values.get("port") or 587)
    security = values.get("security") or "starttls"
    context = ssl.create_default_context()
    server: smtplib.SMTP
    if security == SMTP_SSL:
        server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SECONDS, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS)
    with server:
        server.ehlo()
        if security not in {SMTP_SSL, SMTP_PLAIN}:
            server.starttls(context=context)
            server.ehlo()
        if values.get("username"):
            server.login(values.get("username", ""), values.get("password", ""))
        server.send_message(message)
