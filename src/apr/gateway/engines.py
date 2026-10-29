"""The two lanes. Fast: DuckDB in the gateway process over an Iceberg scan pinned to the
planned snapshots. Spark: Kyuubi (HiveServer2 protocol) in front of Spark on Kubernetes, with
the same Iceberg catalog, the query run as the asking user."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa

from apr import conf
from apr.gateway.planner import Plan, TableScan, scan_view


class LaneUnavailable(Exception):
    pass


@dataclass
class Execution:
    table: pa.Table
    engine_s: float
    scanned_bytes: int


class FastLane:
    name = "fast"

    def __init__(self, catalog=None, threads: int = 2, memory: str = "1GB"):
        self._catalog = catalog
        self.threads = threads
        self.memory = memory

    @property
    def catalog(self):
        if self._catalog is None:
            from apr.lake.catalog import catalog

            self._catalog = catalog()
        return self._catalog

    def _arrow(self, s: TableScan) -> pa.Table:
        t = self.catalog.load_table(s.table)
        if s.snapshot_id is None:
            from apr.contracts.model import all_contracts

            return all_contracts()[s.table].arrow_schema().empty_table()
        return t.scan(
            row_filter=s.row_filter, selected_fields=tuple(sorted(s.columns)), snapshot_id=s.snapshot_id
        ).to_arrow()

    def run(self, plan: Plan) -> Execution:
        t0 = time.perf_counter()
        con = duckdb.connect(config={"threads": self.threads, "memory_limit": self.memory})
        try:
            con.execute("SET TimeZone = 'UTC'")
            for s in plan.scans:
                con.register(scan_view(s), self._arrow(s))
            out = con.sql(plan.physical["fast"]).to_arrow_table()
        finally:
            con.close()
        # the scan reads exactly the planned files and columns, so the planned bytes are what it read
        return Execution(out, time.perf_counter() - t0, plan.est_bytes)


class SparkLane:
    """Spark SQL through Kyuubi. Configured when APR_KYUUBI_HOST is set (the cluster, CI)."""

    name = "spark"

    def __init__(self, host: str | None = None, port: int | None = None):
        cfg = conf.read("platform")["gateway"]["spark"]
        self.host = host or os.environ.get("APR_KYUUBI_HOST")
        self.port = int(port or os.environ.get("APR_KYUUBI_PORT", cfg["kyuubi_port"]))
        self.proxy = bool(cfg.get("user_proxy", True))
        self._down_until = 0.0

    @property
    def available(self) -> bool:
        return bool(self.host)

    def healthy(self) -> bool:
        """Configured and not marked down by a recent connection failure."""
        return self.available and time.monotonic() >= self._down_until

    def mark_down(self, seconds: float = 30.0) -> None:
        self._down_until = time.monotonic() + seconds

    def run(self, plan: Plan) -> Execution:
        if not self.host:
            raise LaneUnavailable("The Spark lane is not configured here (APR_KYUUBI_HOST).")
        from pyhive import hive  # optional dependency: pip install .[spark]

        t0 = time.perf_counter()
        try:
            conn = hive.connect(
                host=self.host,
                port=self.port,
                username=plan.user if self.proxy else "apr",
                configuration={"spark.sql.session.timeZone": "UTC"},
            )
        except Exception as e:  # thrift transport errors
            self.mark_down()
            raise LaneUnavailable(f"Kyuubi at {self.host}:{self.port} is not reachable: {e}") from e
        try:
            cur = conn.cursor()
            cur.execute(plan.physical["spark"])
            rows = cur.fetchall()
            names = [d[0].split(".")[-1] for d in cur.description or []]
        finally:
            conn.close()
        cols: list[Any] = list(zip(*rows)) if rows else [[] for _ in names]
        table = pa.table({n: list(c) for n, c in zip(names, cols)}) if names else pa.table({})
        return Execution(table, time.perf_counter() - t0, plan.est_bytes)
