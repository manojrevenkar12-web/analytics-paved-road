# Runbook: gateway availability burning

Alert: `APR_GatewayAvailability_*`. Accepted queries are failing on the platform's side
(`engine_error`, `lane_unavailable`, `queue_full`). Refusals by policy do not count.

1. Which outcome? `sum by (outcome, lane) (rate(apr_gateway_requests_total[5m]))`.
2. `lane_unavailable`: Kyuubi is down or unreachable. `kubectl -n analytics get pods -l app=kyuubi`,
   its logs, the Spark engine's logs. Queries up to 1 GiB are already running degraded on DuckDB.
3. `queue_full`: a lane is saturated. Check `apr_gateway_inflight` and `apr_gateway_queued`; a
   single user? (`user_concurrency` refusals by user in the audit log). Scale the gateway (HPA) or
   Spark executors; do not raise per-user limits during an incident.
4. `engine_error`: read the messages in the audit log (`status=error`), usually S3 throttling or
   executor loss.
5. The error budget policy (`apr budget`) freezes feature rollouts while the budget is spent.
