from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx
import pytest
import respx
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from config.settings import Settings
from core.domain.errors import ProviderConfigurationError, ProviderUnavailable
from infra.adapters.chat.slack import HttpSlackClient
from infra.adapters.chat.slack_socket import (
    InMemorySocketHeartbeat,
    SlackSocketModeListener,
    SocketConnection,
    connect_websocket,
)
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

_HELLO = {"type": "hello", "num_connections": 1}
_REFRESH = {"type": "disconnect", "reason": "refresh_requested"}
_EVENT_BODY: dict[str, object] = {
    "type": "event_callback",
    "event_id": "Ev123",
    "event": {
        "type": "message",
        "user": "U123",
        "text": "done with the login fix",
        "ts": "1700000000.000001",
        "channel": "D123",
    },
}


class _Stop(BaseException):
    """Breaks run() out of its reconnect loop from inside the injected sleep."""


class _ScriptedConnection:
    """Replays Slack frames, then reports a clean close like a real socket."""

    def __init__(self, frames: list[Mapping[str, object] | str]) -> None:
        self._frames = list(frames)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def recv(self) -> str | bytes:
        # Yield so envelope handlers get scheduled between frames, as on a wire.
        await asyncio.sleep(0)
        if not self._frames:
            raise ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)
        frame = self._frames.pop(0)
        return frame if isinstance(frame, str) else json.dumps(frame)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True


class _SilentConnection(_ScriptedConnection):
    async def recv(self) -> str | bytes:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _Opener:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def open_socket_connection(self) -> str:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else "wss://slack.test/link"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Sink:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[tuple[Mapping[str, object], str]] = []
        self.ambient_correlation_ids: list[str] = []

    async def __call__(self, payload: Mapping[str, object], correlation_id: str) -> None:
        self.events.append((payload, correlation_id))
        self.ambient_correlation_ids.append(current_correlation_id())
        if self.error is not None:
            raise self.error


def _listener(
    connections: list[_ScriptedConnection],
    *,
    sink: _Sink | None = None,
    opener: _Opener | None = None,
    sleeps: list[float] | None = None,
    stop_after_sleeps: int = 1,
) -> SlackSocketModeListener:
    pending = list(connections)
    recorded = sleeps if sleeps is not None else []

    async def connector(url: str) -> SocketConnection:
        return pending.pop(0)

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)
        if len(recorded) >= stop_after_sleeps:
            raise _Stop

    return SlackSocketModeListener(
        url_opener=opener or _Opener([]),
        sink=sink or _Sink(),
        heartbeat=InMemorySocketHeartbeat(),
        connector=connector,
        hello_timeout_seconds=0.05,
        sleep=sleep,
    )


async def test_events_api_envelope_reaches_sink_and_is_acked_after_acceptance() -> None:
    connection = _ScriptedConnection(
        [_HELLO, {"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY}, _REFRESH]
    )
    sink = _Sink()
    listener = _listener([connection], sink=sink)

    outcome = await listener.serve_once()

    assert outcome == "refresh"
    assert len(sink.events) == 1
    payload, correlation_id = sink.events[0]
    # The envelope payload is the exact Events API body the webhook would get,
    # so the shared mapper and event_id dedup apply unchanged.
    assert payload == _EVENT_BODY
    assert sink.ambient_correlation_ids == [correlation_id]
    assert connection.sent == [{"envelope_id": "env-1"}]
    assert connection.closed
    assert await listener.heartbeat.alive()


async def test_failed_event_is_left_unacked_so_slack_redelivers() -> None:
    connection = _ScriptedConnection(
        [_HELLO, {"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY}, _REFRESH]
    )
    listener = _listener([connection], sink=_Sink(error=RuntimeError("database down")))

    await listener.serve_once()

    assert connection.sent == []


async def test_provider_unavailable_event_is_acked_like_the_webhook() -> None:
    connection = _ScriptedConnection(
        [_HELLO, {"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY}, _REFRESH]
    )
    listener = _listener([connection], sink=_Sink(error=ProviderUnavailable("no provider")))

    await listener.serve_once()

    assert connection.sent == [{"envelope_id": "env-1"}]


async def test_non_event_envelopes_are_acked_without_reaching_sink() -> None:
    connection = _ScriptedConnection(
        [
            _HELLO,
            "not json",
            {"type": "slash_commands", "envelope_id": "env-2", "payload": {"command": "/x"}},
            _REFRESH,
        ]
    )
    sink = _Sink()
    listener = _listener([connection], sink=sink)

    await listener.serve_once()

    assert sink.events == []
    assert connection.sent == [{"envelope_id": "env-2"}]


async def test_disabled_socket_mode_and_clean_close_are_reported() -> None:
    disabled = _ScriptedConnection([_HELLO, {"type": "disconnect", "reason": "link_disabled"}])
    closed = _ScriptedConnection([_HELLO])

    assert await _listener([disabled]).serve_once() == "disabled"
    assert await _listener([closed]).serve_once() == "closed"


async def test_missing_hello_times_out_and_closes_the_socket() -> None:
    connection = _SilentConnection([])
    listener = _listener([connection])

    with pytest.raises(TimeoutError):
        await listener.serve_once()

    assert connection.closed
    assert not await listener.heartbeat.alive()


async def test_run_backs_off_exponentially_then_slowest_for_bad_credentials() -> None:
    sleeps: list[float] = []
    opener = _Opener(
        [
            OSError("network unreachable"),
            OSError("network unreachable"),
            ProviderConfigurationError("slack app-level token is not authorized"),
        ]
    )
    listener = _listener([], opener=opener, sleeps=sleeps, stop_after_sleeps=3)

    with pytest.raises(_Stop):
        await listener.run()

    assert sleeps == [1.0, 2.0, 60.0]


async def test_run_reconnects_at_once_on_refresh_and_resets_backoff_after_hello() -> None:
    sleeps: list[float] = []
    opener = _Opener([OSError("blip"), "wss://slack.test/a", "wss://slack.test/b"])
    first = _ScriptedConnection([_HELLO, _REFRESH])
    second = _ScriptedConnection([_HELLO])
    listener = _listener([first, second], opener=opener, sleeps=sleeps, stop_after_sleeps=2)

    with pytest.raises(_Stop):
        await listener.run()

    # 1.0 after the blip; none after the refresh; the clean close waits the
    # reset initial delay rather than the doubled one.
    assert sleeps == [1.0, 1.0]
    assert opener.calls == 3
    assert first.closed and second.closed


@respx.mock
async def test_http_slack_client_opens_socket_mode_url_with_app_token() -> None:
    client = HttpSlackClient(bot_token="xapp-test", base_url="https://slack.test/api")
    route = respx.post("https://slack.test/api/apps.connections.open").mock(
        return_value=httpx.Response(200, json={"ok": True, "url": "wss://slack.test/link?t=1"})
    )

    assert await client.open_socket_connection() == "wss://slack.test/link?t=1"
    assert route.calls.last.request.headers["authorization"] == "Bearer xapp-test"


@respx.mock
async def test_http_slack_client_maps_bot_token_on_socket_open_to_configuration_error() -> None:
    client = HttpSlackClient(bot_token="xoxb-wrong", base_url="https://slack.test/api")
    respx.post("https://slack.test/api/apps.connections.open").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "not_allowed_token_type"})
    )

    with pytest.raises(ProviderConfigurationError, match="app-level token"):
        await client.open_socket_connection()


async def test_socket_events_share_webhook_intake_and_dedup_redelivery(
    settings: Settings,
) -> None:
    registry = ServiceRegistry(
        settings.model_copy(
            update={
                "chat_provider": "fake",
                "issue_tracker_provider": "fake",
                "llm_provider": "fake",
            }
        )
    )
    await registry.status_collector().start_checkin(
        tenant_id=settings.tenant_id,
        developer_id="dev-1",
        chat_external_id="U123",
        correlation_id="corr-socket",
        asked_at=datetime.now(tz=UTC),
    )
    outcomes: list[str] = []

    async def sink(payload: Mapping[str, object], correlation_id: str) -> None:
        result = await registry.accept_chat_event("slack", payload, correlation_id)
        outcomes.append(result.status)

    # Slack redelivers an unacked event in a new envelope with the same event_id.
    connection = _ScriptedConnection(
        [
            _HELLO,
            {"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY},
            {"type": "events_api", "envelope_id": "env-2", "payload": _EVENT_BODY},
            _REFRESH,
        ]
    )
    listener = _listener([connection])
    listener.sink = sink

    await listener.serve_once()

    assert outcomes == ["processed", "duplicate"]
    assert connection.sent == [{"envelope_id": "env-1"}, {"envelope_id": "env-2"}]


async def test_listener_speaks_socket_mode_over_a_real_websocket() -> None:
    # Loopback Slack: hello, one event, wait for its ack, then ask for a refresh;
    # the second connection proves the listener reconnects on its own.
    acks: list[object] = []
    connections = 0
    second_connection = asyncio.Event()

    async def slack(websocket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        if connections > 1:
            second_connection.set()
            await websocket.wait_closed()
            return
        await websocket.send(json.dumps(_HELLO))
        await websocket.send(
            json.dumps({"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY})
        )
        acks.append(json.loads(await websocket.recv()))
        await websocket.send(json.dumps(_REFRESH))
        await websocket.wait_closed()

    async with serve(slack, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        sink = _Sink()
        listener = SlackSocketModeListener(
            url_opener=_Opener([f"ws://127.0.0.1:{port}/link"] * 2),
            sink=sink,
            heartbeat=InMemorySocketHeartbeat(),
            connector=connect_websocket,
        )
        task = asyncio.create_task(listener.run())
        try:
            await asyncio.wait_for(second_connection.wait(), timeout=5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert acks == [{"envelope_id": "env-1"}]
    assert [payload for payload, _ in sink.events] == [_EVENT_BODY]
    assert connections == 2


async def test_heartbeat_failure_does_not_drop_a_healthy_socket() -> None:
    class _BrokenHeartbeat(InMemorySocketHeartbeat):
        async def beat(self) -> None:
            raise ConnectionError("redis unavailable")

    connection = _ScriptedConnection(
        [_HELLO, {"type": "events_api", "envelope_id": "env-1", "payload": _EVENT_BODY}, _REFRESH]
    )
    listener = _listener([connection])
    listener.heartbeat = _BrokenHeartbeat()

    assert await listener.serve_once() == "refresh"
    assert connection.sent == [{"envelope_id": "env-1"}]
