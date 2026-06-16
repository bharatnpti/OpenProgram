from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


@dataclass(frozen=True)
class HttpMetrics:
    registry: CollectorRegistry
    requests_total: Counter
    request_latency_seconds: Histogram
    app_info: Gauge

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
        "pulseops_http_requests_total",
        "Total HTTP requests handled by the PulseOps API.",
        ("method", "route", "status_code"),
        registry=registry,
    )
    request_latency_seconds = Histogram(
        "pulseops_http_request_duration_seconds",
        "HTTP request duration in seconds.",
        ("method", "route"),
        registry=registry,
    )
    app_info = Gauge(
        "pulseops_info",
        "PulseOps application information.",
        ("environment",),
        registry=registry,
    )
    app_info.labels(environment=environment).set(1)
    return HttpMetrics(
        registry=registry,
        requests_total=requests_total,
        request_latency_seconds=request_latency_seconds,
        app_info=app_info,
    )
