# Runbook: Spark-lane latency burning

Alert: `APR_SparkLaneLatency_*`. Fewer than 90% of Spark-lane queries finish within 2 minutes.

1. Engine start-up: Kyuubi share level and idle timeout; are engines being recycled?
2. Executors: dynamic allocation limits, pending pods, node capacity.
3. Large scans: the audit log's `est_bytes` for slow queries; a mart may be missing.
