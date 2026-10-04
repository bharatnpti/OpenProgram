"""Readiness of the OpenAI-compatible endpoint the LLM adapter calls.

The adapter POSTs ``{base_url}/v1/chat/completions``. Every setup it is pointed
at -- a LiteLLM gateway, OpenAI or another OpenAI-compatible API, the local mock
(``scripts/mock_llm.py``) -- also answers ``GET {base_url}/v1/models`` with the
same bearer key. That is the probe: it shows the endpoint is reachable *and*
accepts the key, and it never spends a token. An endpoint that does not list
models falls back to LiteLLM's unauthenticated ``/health/readiness``.

Reasons are built from fixed text, HTTP status codes and exception type names
only -- never a response body or exception message, either of which can echo
the key back.

One failed probe right after a successful one does not degrade ``/ready``: it
reports ready with a note, and the second failure in a row reports degraded.
A distant endpoint can miss the probe's short budget now and then while every
real call, which waits much longer, succeeds. ``LlmReadinessHistory`` carries
that count between ``/ready`` calls, which each build a fresh probe.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import httpx
import structlog
from opentelemetry import trace

from core.ports.readiness import ReadinessReport

_logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("openprogram.adapters.llm.readiness")

MODELS_PATH = "/v1/models"
GATEWAY_READINESS_PATH = "/health/readiness"
# Below the registry's 3 s bound on every probe, so this probe's own reason is
# what /ready shows rather than a generic "timed out".
DEFAULT_TIMEOUT_SECONDS = 2.5

# A restricted OpenAI key can be allowed to chat yet not to list models; the
# 401 then names the missing scope rather than rejecting the key.
_MISSING_SCOPE_MARKERS = ("missing scopes", "insufficient permissions")

# /ready degrades on this many failed probes in a row. With 2, a lone failure
# right after a success still reports ready, with a note saying so.
FAILURES_BEFORE_DEGRADED = 2

# Last outcome per endpoint origin, so a failing probe logs when the reason
# changes instead of on every /ready poll.
_last_outcome: dict[str, ReadinessReport] = {}


@dataclass
class LlmReadinessHistory:
    """What earlier probes of the endpoint saw, kept between ``/ready`` calls.

    The registry builds new probes for every ``/ready`` call and owns one
    history for its lifetime, so a probe can tell a lone miss from an outage.
    Nothing is tolerated before a first success: an endpoint this process has
    never reached is degraded from its first failed probe on, so a fresh
    process with a wrong key or URL shows it on the first ``/ready``.
    """

    failures_before_degraded: int = FAILURES_BEFORE_DEGRADED
    consecutive_failures: int = 0
    has_succeeded: bool = False
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def judge(self, probed: ReadinessReport) -> ReadinessReport:
        """Record one probe's outcome and return what ``/ready`` reports for it."""
        with self._lock:
            if probed.ready:
                self.consecutive_failures = 0
                self.has_succeeded = True
                return probed
            self.consecutive_failures += 1
            failures_left = self.failures_before_degraded - self.consecutive_failures
            if self.has_succeeded and failures_left > 0:
                return ReadinessReport(ready=True, detail=_missed_detail(probed, failures_left))
            return probed


@dataclass(frozen=True)
class LlmEndpointReadinessProbe:
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    # A probe built on its own has no past: every failure it sees degrades.
    history: LlmReadinessHistory = field(
        default_factory=LlmReadinessHistory, repr=False, compare=False
    )

    async def check(self) -> bool:
        return (await self.report()).ready

    async def report(self) -> ReadinessReport:
        with _tracer.start_as_current_span("readiness.llm") as span:
            probed = await self._probe()
            outcome = self.history.judge(probed)
            span.set_attribute("readiness.ready", outcome.ready)
            span.set_attribute("readiness.probe_ready", probed.ready)
            _log_change(self.base_url, outcome, missed=outcome.ready and not probed.ready)
            return outcome

    async def _probe(self) -> ReadinessReport:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._request()
        except (TimeoutError, httpx.TimeoutException):
            return _not_ready(f"unreachable: no response within {self.timeout_seconds:g}s")
        except httpx.UnsupportedProtocol:
            return _not_ready("unreachable: the base URL is not an http(s) URL")
        except httpx.TransportError as exc:
            return _not_ready(f"unreachable: connection failed ({type(exc).__name__})")
        except Exception as exc:
            return _not_ready(f"error: the probe failed ({type(exc).__name__})")

    async def _request(self) -> ReadinessReport:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.get(MODELS_PATH, headers=headers)
            if response.status_code not in (404, 405):
                return _from_models_response(response, key_configured=bool(self.api_key))
            # No model list here: a gateway in front of the provider may still
            # expose LiteLLM's readiness route, which needs no key.
            gateway = await client.get(GATEWAY_READINESS_PATH)
            if gateway.is_success:
                return ReadinessReport(ready=True)
            return _not_ready(_no_route_reason(self.base_url, gateway.status_code))


def _from_models_response(response: httpx.Response, *, key_configured: bool) -> ReadinessReport:
    status = response.status_code
    if response.is_success:
        if _is_model_list(response):
            return ReadinessReport(ready=True)
        return _not_ready(
            f"unexpected_response: HTTP {status} from {MODELS_PATH} is not a model list"
            " -- is the base URL an OpenAI-compatible API?"
        )
    if status in (401, 403):
        return _from_auth_failure(response, key_configured=key_configured)
    if status == 429:
        return _not_ready(
            "rate_limited: the endpoint is throttling this key or it is out of quota (HTTP 429)"
        )
    if status >= 500:
        return _not_ready(f"upstream_error: the endpoint is reachable but failing (HTTP {status})")
    return _not_ready(f"unexpected_response: HTTP {status} from {MODELS_PATH}")


def _from_auth_failure(response: httpx.Response, *, key_configured: bool) -> ReadinessReport:
    status = response.status_code
    if _names_missing_scope(response):
        return ReadinessReport(
            ready=True,
            detail=(
                "authenticated: the key is accepted but may not list models,"
                " so chat itself was not verified"
            ),
        )
    if not key_configured:
        return _not_ready(
            f"unauthorized: the endpoint requires an API key and none is configured (HTTP {status})"
        )
    return _not_ready(f"unauthorized: the endpoint rejected the API key (HTTP {status})")


def _no_route_reason(base_url: str, status: int) -> str:
    reason = (
        f"unexpected_response: the endpoint serves neither {MODELS_PATH}"
        f" nor {GATEWAY_READINESS_PATH} (HTTP {status})"
    )
    if _path(base_url).rstrip("/").endswith("/v1"):
        reason += " -- the base URL should not end in /v1; the adapter appends it"
    return reason


def _is_model_list(response: httpx.Response) -> bool:
    payload = _json_object(response)
    return payload is not None and isinstance(payload.get("data"), list)


def _names_missing_scope(response: httpx.Response) -> bool:
    payload = _json_object(response)
    error = payload.get("error") if payload is not None else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _MISSING_SCOPE_MARKERS)


def _json_object(response: httpx.Response) -> dict[str, object] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _not_ready(detail: str) -> ReadinessReport:
    return ReadinessReport(ready=False, detail=detail)


def _missed_detail(failed: ReadinessReport, failures_left: int) -> str:
    # Fixed text around the failed probe's own (already safe) reason.
    reason = failed.detail or "no reason given"
    more = "one more failure" if failures_left == 1 else f"{failures_left} more failures"
    return (
        f"unconfirmed: this probe failed ({reason}) after a success;"
        f" {more} in a row reports degraded"
    )


def _log_change(base_url: str, outcome: ReadinessReport, *, missed: bool = False) -> None:
    target = _origin(base_url)
    previous = _last_outcome.get(target)
    _last_outcome[target] = outcome
    if outcome == previous:
        return
    if outcome.ready and outcome.detail is None:
        if previous is not None:
            _logger.info("llm_readiness_recovered", target=target)
        return
    if missed:
        event = "llm_readiness_probe_missed"
    elif not outcome.ready:
        event = "llm_readiness_degraded"
    else:
        event = "llm_readiness_unverified"
    _logger.warning(event, target=target, ready=outcome.ready, detail=outcome.detail)


def _origin(base_url: str) -> str:
    """Scheme, host and port only: userinfo, path and query stay out of logs."""
    try:
        url = httpx.URL(base_url)
    except Exception:
        return "<invalid base url>"
    port = f":{url.port}" if url.port is not None else ""
    return f"{url.scheme}://{url.host}{port}"


def _path(base_url: str) -> str:
    try:
        return httpx.URL(base_url).path
    except Exception:
        return ""
