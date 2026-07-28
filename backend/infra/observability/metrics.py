from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


@dataclass(frozen=True)
class HttpMetrics:
    registry: CollectorRegistry
    requests_total: Counter
    request_latency_seconds: Histogram
    app_info: Gauge
    dead_letters_open: Gauge
    inbound_events_stuck: Gauge
    writeback_applied: Gauge

    def set_workflow_backlog(self, dead_letters_open: int, inbound_events_stuck: int) -> None:
        self.dead_letters_open.set(dead_letters_open)
        self.inbound_events_stuck.set(inbound_events_stuck)

    def set_writeback_applied(self, applied_count: int) -> None:
        self.writeback_applied.set(applied_count)

    def observe_request(self, method: str, route: str, status_code: int, duration: float) -> None:
        labels = {
            "method": method,
            "route": route,
            "status_code": str(status_code),
        }
        self.requests_total.labels(**labels).inc()
        self.request_latency_seconds.labels(method=method, route=route).observe(duration)

    def render(self) -> bytes:
        return generate_latest(self.registry)


def build_http_metrics(environment: str) -> HttpMetrics:
    registry = CollectorRegistry()
    requests_total = Counter(
        "openprogram_http_requests_total",
        "Total HTTP requests handled by the OpenProgram API.",
        ("method", "route", "status_code"),
        registry=registry,
    )
    request_latency_seconds = Histogram(
        "openprogram_http_request_duration_seconds",
        "HTTP request duration in seconds.",
        ("method", "route"),
        registry=registry,
    )
    app_info = Gauge(
        "openprogram_info",
        "OpenProgram application information.",
        ("environment",),
        registry=registry,
    )
    app_info.labels(environment=environment).set(1)
    dead_letters_open = Gauge(
        "openprogram_dead_letters_open",
        "Open (un-rearmed) workflow dead-letter records.",
        registry=registry,
    )
    inbound_events_stuck = Gauge(
        "openprogram_inbound_events_stuck",
        "Inbound chat events past the sweeper grace window awaiting finalization.",
        registry=registry,
    )
    writeback_applied = Gauge(
        "openprogram_writeback_applied",
        "Issue-tracker updates applied via check-in write-back (adoption signal).",
        registry=registry,
    )
    return HttpMetrics(
        registry=registry,
        requests_total=requests_total,
        request_latency_seconds=request_latency_seconds,
        app_info=app_info,
        dead_letters_open=dead_letters_open,
        inbound_events_stuck=inbound_events_stuck,
        writeback_applied=writeback_applied,
    )
