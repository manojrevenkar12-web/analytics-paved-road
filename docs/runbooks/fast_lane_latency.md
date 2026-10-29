# Runbook: fast-lane latency burning

Alert: `APR_FastLaneLatency_*`. Fewer than 95% of fast-lane queries finish within 2 s.

1. Catalog slow? Planning loads table metadata (cached `catalog_cache_s`); check the Iceberg REST
   catalog's latency. The `catalog_latency` drill shows what an uncached gateway would suffer.
2. Manifests piling up? `apr maintain` compacts tables above `lake.maintenance.max_manifests`.
3. Pods CPU-bound? Check the HPA and `apr_gateway_inflight{lane="fast"}`.
4. A lowered `fast_lane_max_bytes` sends more to Spark; a raised one makes the fast lane slow.
