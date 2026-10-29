"""Prometheus metrics. The SLOs in config/slo.yaml are written over these names."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

REQUESTS = Counter(
    "apr_gateway_requests_total", "Queries by lane, role and outcome", ["lane", "role", "outcome"], registry=REGISTRY
)
REFUSALS = Counter(
    "apr_gateway_refusals_total", "Queries refused before running, by rule", ["rule", "role"], registry=REGISTRY
)
LATENCY = Histogram(
    "apr_gateway_query_duration_seconds",
    "End-to-end query time by lane",
    ["lane"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)
SCANNED = Counter("apr_gateway_scanned_bytes_total", "Bytes read from the lake", ["lane", "role"], registry=REGISTRY)
CACHE = Counter("apr_gateway_cache_total", "Result cache lookups", ["result"], registry=REGISTRY)
INFLIGHT = Gauge("apr_gateway_inflight", "Queries running by lane", ["lane"], registry=REGISTRY)
QUEUED = Gauge("apr_gateway_queued", "Queries waiting for a slot by lane", ["lane"], registry=REGISTRY)
FRESHNESS = Gauge(
    "apr_product_last_published_timestamp_seconds",
    "When the newest published batch of a data product landed",
    ["product"],
    registry=REGISTRY,
)
FRESH_DAY = Gauge(
    "apr_product_data_through_timestamp_seconds",
    "End of the newest business day a data product contains",
    ["product"],
    registry=REGISTRY,
)
FRESH_ON_TIME = Gauge(
    "apr_product_freshness_on_time",
    "1 if the newest batch was published by the contract's time",
    ["product"],
    registry=REGISTRY,
)
