"""The query gateway: plan, admit, run on a lane (or answer from cache), meter and audit."""

from __future__ import annotations

import datetime as dt
import decimal
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import pyarrow as pa

from apr import conf
from apr.gateway import audit, metrics
from apr.gateway.cache import ResultCache
from apr.gateway.engines import FastLane, LaneUnavailable, SparkLane
from apr.gateway.planner import Plan, Planner, Refused


@dataclass
class Result:
    query_id: str
    status: str  # ok | refused | error
    user: str
    role: str | None = None
    rule: str | None = None
    message: str = ""
    http_status: int = 200
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    lane: str | None = None
    cached: bool = False
    est_bytes: int = 0
    scanned_bytes: int = 0
    duration_s: float = 0.0
    snapshots: dict[str, int | None] = field(default_factory=dict)
    masked: list[str] = field(default_factory=list)
    sql: str = ""
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, float) and v != v:
        return None
    return v


def to_rows(t: pa.Table) -> tuple[list[str], list[list[Any]]]:
    cols = t.column_names
    data = [t.column(i).to_pylist() for i in range(t.num_columns)]
    return cols, [[_jsonable(v) for v in row] for row in zip(*data)] if data else []


class Gateway:
    def __init__(
        self,
        planner: Planner | None = None,
        fast: FastLane | None = None,
        spark: SparkLane | None = None,
        cache: ResultCache | None = None,
        audit_log: bool = True,
    ):
        cfg = conf.read("platform")["gateway"]
        self.planner = planner or Planner()
        self.fast = fast or FastLane()
        self.spark = spark or SparkLane()
        self.cache = cache or ResultCache(int(cfg["cache_max_bytes"]))
        self.audit_log = audit_log
        self.degrade_max = int(cfg["degrade_max_bytes"])

    def plan(self, sql: str, user: str) -> Plan:
        return self.planner.plan(sql, user)

    def query(self, sql: str, user: str, plan: Plan | None = None) -> Result:
        qid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        res = Result(qid, "ok", user, sql=sql)
        try:
            plan = plan or self.plan(sql, user)
            res.role, res.lane, res.est_bytes = plan.role, plan.lane, plan.est_bytes
            res.snapshots, res.masked = plan.snapshots, plan.masked
            table = self.cache.get(plan.fingerprint)
            if table is not None:
                res.cached = True
                metrics.CACHE.labels("hit").inc()
            else:
                metrics.CACHE.labels("miss").inc()
                lane_name = plan.lane
                if lane_name == "spark" and not self.spark.healthy() and plan.est_bytes <= self.degrade_max:
                    lane_name = "fast"  # degraded: slower, but answered
                    res.degraded = True
                    res.lane = "fast"
                lane = self.fast if lane_name == "fast" else self.spark
                metrics.INFLIGHT.labels(lane_name).inc()
                try:
                    ex = lane.run(plan)
                finally:
                    metrics.INFLIGHT.labels(lane_name).dec()
                table = ex.table
                res.scanned_bytes = ex.scanned_bytes
                metrics.SCANNED.labels(plan.lane, plan.role).inc(ex.scanned_bytes)
                self.cache.put(plan.fingerprint, table)
            res.columns, res.rows = to_rows(table)
            res.row_count = len(res.rows)
            res.truncated = plan.limit_added and res.row_count >= plan.row_limit
            if res.truncated:
                res.message = f"Showing the first {plan.row_limit:,} rows; aggregate or add a LIMIT for fewer."
        except Refused as e:
            res.status, res.rule, res.message, res.http_status = "refused", e.rule, e.message, e.status
            res.role = res.role or self.planner.policy.cfg["users"].get(user)
            metrics.REFUSALS.labels(e.rule, res.role or "unknown").inc()
        except LaneUnavailable as e:
            res.status, res.rule, res.message, res.http_status = "error", "lane_unavailable", str(e), 503
        except Exception as e:  # engine errors are the platform's failures, not the user's
            res.status, res.rule, res.http_status = "error", "engine_error", 500
            res.message = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
        res.duration_s = time.perf_counter() - t0
        outcome = res.status if res.status != "error" else res.rule
        label = "cache" if res.cached else (res.lane or "none")
        metrics.REQUESTS.labels(label, res.role or "unknown", outcome).inc()
        if res.lane and res.status == "ok" and not res.cached:
            metrics.LATENCY.labels(res.lane).observe(res.duration_s)
        if self.audit_log:
            audit.append(
                {
                    "query_id": qid,
                    "user": user,
                    "role": res.role,
                    "status": res.status,
                    "rule": res.rule,
                    "sql": sql,
                    "lane": res.lane,
                    "cached": res.cached,
                    "est_bytes": res.est_bytes,
                    "scanned_bytes": res.scanned_bytes,
                    "rows": res.row_count,
                    "duration_s": round(res.duration_s, 4),
                    "snapshots": res.snapshots,
                    "masked": res.masked,
                    "degraded": res.degraded,
                    "fingerprint": plan.fingerprint if plan else None,
                }
            )
        return res
