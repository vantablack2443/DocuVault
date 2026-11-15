"""Prometheus metrics. Labels are low-cardinality on purpose: route templates,
never raw paths, tenant ids or anything derived from request content."""

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "docuvault_http_requests_total",
    "HTTP requests handled.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "docuvault_http_request_duration_seconds",
    "Time to produce the response (for streams: until the headers).",
    ["route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)
GATEWAY_REQUESTS = Counter(
    "docuvault_gateway_requests_total",
    "Chat completions by answering provider, outcome and cache use.",
    ["provider", "status", "cache"],
    registry=REGISTRY,
)
REDACTIONS = Counter(
    "docuvault_redactions_total",
    "Values replaced by placeholders, by PII type.",
    ["type"],
    registry=REGISTRY,
)
GUEST_BUDGET_EXHAUSTED = Counter(
    "docuvault_guest_budget_exhausted_total",
    "Guest calls answered by replay because the daily LLM allowance was used up.",
    registry=REGISTRY,
)

UNMATCHED_ROUTE = "unmatched"
