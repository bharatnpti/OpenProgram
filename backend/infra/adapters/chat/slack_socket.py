"""Slack Socket Mode intake: receive Events API envelopes over an outbound WebSocket.

Socket Mode lets OpenProgram receive developer replies without a public Request
URL. Each ``events_api`` envelope carries the same body Slack would POST to
``/webhooks/chat/slack``, so it goes through the same mapping, ``event_id``
dedup and reply coalescing; only the transport and its authentication differ
(an app-level token instead of a request signature).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

import structlog
from opentelemetry import trace
from redis.asyncio import Redis
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from core.domain.errors import ProviderConfigurationError, ProviderUnavailable
from infra.observability.tracing import correlation_scope

_tracer = trace.get_tracer("openprogram.adapters.chat.slack_socket")
_logger = structlog.get_logger(__name__)

# Why serve_once() stopped. "refresh": Slack asked us to reconnect (routine,
# every few hours). "closed": the socket closed cleanly after a hello.
# "disabled": Socket Mode was switched off in the Slack app config.
_ServeOutcome = Literal["refresh", "closed", "disabled"]


class SocketUrlOpener(Protocol):
    async def open_socket_connection(self) -> str: ...


class SocketConnection(Protocol):
    async def recv(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...

    async def close(self) -> None: ...


class SocketHeartbeat(Protocol):
    async def beat(self) -> None: ...

    async def alive(self) -> bool: ...


SocketConnector = Callable[[str], Awaitable[SocketConnection]]
ChatEventSink = Callable[[Mapping[str, object], str], Awaitable[object]]


async def connect_websocket(url: str) -> SocketConnection:
    # proxy=True is the websockets default: it honours HTTPS_PROXY, which is the
    # only way out of a corporate network. The default 20s ping keepalive is what
    # notices a silently dead link.
    return await connect(url, open_timeout=10.0)


@dataclass
class InMemorySocketHeartbeat:
    beats: int = 0

    async def beat(self) -> None:
        self.beats += 1

    async def alive(self) -> bool:
        return self.beats > 0


@dataclass(frozen=True)
class RedisSocketHeartbeat:
    """Shared "some worker holds a live socket" flag, read by the API's /ready.

    Every connected worker refreshes the same key and nobody deletes it, so with
    several replicas the flag only drops once all of them have been
    disconnected for a full TTL.
    """

    tenant_id: str
    client: Redis
    ttl_seconds: int = 90

    async def beat(self) -> None:
        await self.client.set(
            self._key(),
            datetime.now(tz=UTC).isoformat(),
            ex=self.ttl_seconds,
        )

    async def alive(self) -> bool:
        return bool(await self.client.exists(self._key()))

    def _key(self) -> str:
        return f"openprogram:slack:socket:{self.tenant_id}:heartbeat"


@dataclass(frozen=True)
class SocketHeartbeatReadinessProbe:
    heartbeat: SocketHeartbeat

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.slack_socket"):
            return await self.heartbeat.alive()


@dataclass
class SlackSocketModeListener:
    url_opener: SocketUrlOpener
    sink: ChatEventSink
    heartbeat: SocketHeartbeat
    connector: SocketConnector = connect_websocket
    hello_timeout_seconds: float = 10.0
    heartbeat_interval_seconds: float = 30.0
    reconnect_backoff_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    drain_timeout_seconds: float = 2.5
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _hello_received: bool = field(default=False, init=False)
    _in_flight: set[asyncio.Task[None]] = field(default_factory=set, init=False)

    async def run(self) -> None:
        """Hold a Socket Mode connection open indefinitely, reconnecting on loss.

        Slack-side failures never escape: a bad token or disabled Socket Mode is
        logged and retried at the slowest backoff, so a Slack problem shows up
        as a degraded /ready instead of stopping the worker's other workflows.
        """
        delay = self.reconnect_backoff_seconds
        while True:
            try:
                outcome = await self.serve_once()
            except ProviderConfigurationError as exc:
                _logger.error("slack.socket.misconfigured", error=str(exc))
                await self.sleep(self.reconnect_max_seconds)
                continue
            except Exception as exc:
                if self._hello_received:
                    delay = self.reconnect_backoff_seconds
                _logger.warning(
                    "slack.socket.connection_lost",
                    error_type=type(exc).__name__,
                    retry_in_seconds=delay,
                )
                await self.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_seconds)
                continue
            delay = self.reconnect_backoff_seconds
            if outcome == "disabled":
                _logger.error(
                    "slack.socket.misconfigured",
                    error="Socket Mode is disabled in the Slack app configuration",
                )
                await self.sleep(self.reconnect_max_seconds)
            elif outcome == "closed":
                await self.sleep(delay)
            # A refresh is routine, so reconnect at once to keep the gap short.

    async def serve_once(self) -> _ServeOutcome:
        """Open one connection and serve it until Slack ends it."""
        self._hello_received = False
        url = await self.url_opener.open_socket_connection()
        connection = await self.connector(url)
        try:
            await self._await_hello(connection)
            self._hello_received = True
            _logger.info("slack.socket.connected")
            await self._beat()
            keepalive = asyncio.create_task(self._keep_heartbeat())
            try:
                return await self._receive(connection)
            finally:
                keepalive.cancel()
                await self._drain_in_flight()
        finally:
            await connection.close()

    async def _await_hello(self, connection: SocketConnection) -> None:
        async with asyncio.timeout(self.hello_timeout_seconds):
            while True:
                message = _decode(await connection.recv())
                if message is None:
                    continue
                if message.get("type") == "hello":
                    return
                if message.get("type") == "disconnect":
                    raise ProviderUnavailable("slack socket disconnected before hello")

    async def _receive(self, connection: SocketConnection) -> _ServeOutcome:
        while True:
            try:
                raw = await connection.recv()
            except ConnectionClosedOK:
                return "closed"
            message = _decode(raw)
            if message is None:
                continue
            kind = message.get("type")
            if kind == "disconnect":
                return "disabled" if message.get("reason") == "link_disabled" else "refresh"
            envelope_id = message.get("envelope_id")
            if isinstance(envelope_id, str) and envelope_id:
                task = asyncio.create_task(
                    self._handle_envelope(
                        connection,
                        envelope_id,
                        kind if isinstance(kind, str) else "",
                        message.get("payload"),
                    )
                )
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def _handle_envelope(
        self,
        connection: SocketConnection,
        envelope_id: str,
        kind: str,
        payload: object,
    ) -> None:
        # Ack only after the event is durably accepted: an unacked envelope is
        # redelivered by Slack, and event_id dedup absorbs the repeat.
        with _tracer.start_as_current_span("slack.socket.envelope") as span:
            span.set_attribute("slack.socket.envelope_type", kind)
            if kind == "events_api" and isinstance(payload, Mapping):
                correlation_id = str(uuid4())
                async with correlation_scope(correlation_id):
                    try:
                        await self.sink(payload, correlation_id)
                    except ProviderUnavailable:
                        # Same as the webhook's "provider-unavailable": a retry
                        # would hit the same provider state, so ack and move on.
                        _logger.warning("slack.socket.event_ignored", envelope_id=envelope_id)
                    except Exception as exc:
                        _logger.error(
                            "slack.socket.event_failed",
                            envelope_id=envelope_id,
                            error_type=type(exc).__name__,
                        )
                        return
            try:
                await connection.send(json.dumps({"envelope_id": envelope_id}))
            except ConnectionClosed:
                # The socket went away mid-ack; Slack redelivers on the next one.
                _logger.info("slack.socket.ack_dropped", envelope_id=envelope_id)

    async def _drain_in_flight(self) -> None:
        # Let envelopes already being handled ack on this socket before it
        # closes; anything slower is redelivered by Slack and deduped.
        if self._in_flight:
            await asyncio.wait(set(self._in_flight), timeout=self.drain_timeout_seconds)

    async def _keep_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            await self._beat()

    async def _beat(self) -> None:
        # Best effort: the heartbeat only feeds /ready, so a Redis blip must not
        # drop a healthy Slack socket.
        try:
            await self.heartbeat.beat()
        except Exception as exc:
            _logger.warning("slack.socket.heartbeat_failed", error_type=type(exc).__name__)


def _decode(raw: str | bytes) -> Mapping[str, object] | None:
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return message if isinstance(message, Mapping) else None
