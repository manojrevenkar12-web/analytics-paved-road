"""Discrete-event simulation of the analytical plane under a realistic workload.

The same arrival stream is played twice: through the gateway (real planner on the real lake,
lanes, admission, cache, refusals with retries) and against the baseline (every query sent as
written to one Spark cluster, the status quo the gateway replaces). Access decisions, byte
estimates and result rows come from real planning and real execution on the lake, memoised per
distinct query; only queueing and execution time are modelled (config/workload.yaml).
"""

from __future__ import annotations

import copy
import datetime as dt
import heapq
import itertools
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from apr import conf
from apr.contracts.model import all_contracts
from apr.gateway.core import Gateway
from apr.gateway.planner import Planner, Policy, Refused

MIN = 60.0
DAY = 86400.0


@dataclass
class Known:
    """What real planning and execution said about one (query, role)."""

    status: str
    rule: str | None = None
    message: str = ""
    lane: str | None = None
    est_bytes: int = 0
    rows: int = 0
    fingerprint: str = ""
    pii_cells: int = 0  # baseline only: PII cells this role may not see in clear
    leak: bool = False  # gateway only: a clear PII value in a result for a role that may not see it


class SimExecutor:
    """Runs the fast-lane physical SQL for the simulation against every table loaded once into
    one DuckDB connection. The per-scan row filters only prune files; the query keeps its WHERE,
    so results equal a FastLane run (tests/test_workload.py checks it), much faster."""

    def __init__(self, catalog):
        import duckdb

        self.con = duckdb.connect(config={"threads": 2})
        self.con.execute("SET TimeZone = 'UTC'")
        self.catalog = catalog
        self.loaded: set[str] = set()
        self.views: set[str] = set()

    def run(self, plan):
        from apr.gateway.engines import Execution
        from apr.gateway.planner import scan_view

        for s in plan.scans:
            full = s.table.replace(".", "__") + "__all"
            if full not in self.loaded:
                self.con.register(full, self.catalog.load_table(s.table).scan().to_arrow())
                self.loaded.add(full)
            v = scan_view(s)
            if v not in self.views:
                self.con.execute(f"CREATE OR REPLACE VIEW {v} AS SELECT * FROM {full}")
                self.views.add(v)
        return Execution(self.con.sql(plan.physical["fast"]).to_arrow_table(), 0.0, plan.est_bytes)


@dataclass
class Faults:
    """Injected for drills; times are seconds since the start of the simulation."""

    spark_down: list[tuple[float, float]] = field(default_factory=list)
    catalog_slow: list[tuple[float, float, float]] = field(default_factory=list)  # start, end, extra seconds
    storm: list[tuple[float, float, str, float]] = field(default_factory=list)  # start, end, template, per second
    catalog_cache: bool = True
    admission: bool = True
    degrade: bool = True


@dataclass
class Outcome:
    t: float
    world: str
    user: str
    role: str
    template: str
    status: str  # ok | refused | queue_full | user_concurrency | lane_unavailable
    rule: str | None
    lane: str | None
    latency: float
    scanned: int
    cached: bool
    retry: bool
    human: bool
    degraded: bool = False
    pii_cells: int = 0


class Workload:
    def __init__(self, cfg: dict | None = None, gateway: Gateway | None = None):
        self.cfg = cfg or copy.deepcopy(conf.read("workload"))
        self.rng = np.random.default_rng(int(self.cfg["seed"]))
        self.scale: dict[str, float] = {k: float(v) for k, v in self.cfg["scale"].items()}
        roles = copy.deepcopy(conf.read("roles"))
        self.users: list[tuple[str, str]] = []
        for role, n in self.cfg["users"].items():
            for i in range(int(n)):
                name = f"{role[:3]}{i:03d}"
                self.users.append((name, role))
                roles["users"][name] = role
        self.roles_cfg = roles
        cat = gateway.planner.catalog if gateway else None
        self.gw = gateway or Gateway(
            planner=Planner(policy=Policy(roles), catalog=cat, size_factor=self.scale), audit_log=False
        )
        if gateway is not None:
            self.gw.planner.policy = Policy(roles)
            self.gw.planner.size_factor = self.scale
        open_roles = copy.deepcopy(roles)
        everything = list(conf.read("roles")["classes"])
        for r in open_roles["roles"].values():
            r["clear"], r["masked"], r["denied"] = everything, [], []
        self.base_planner = Planner(
            policy=Policy(open_roles), catalog=self.gw.planner.catalog, enforce=False, size_factor=self.scale
        )
        self.fast = SimExecutor(self.gw.planner.catalog)
        self.known_gw: dict[tuple[str, str], Known] = {}
        self.known_base: dict[tuple[str, str], Known] = {}
        self._params()

    # ---- parameters drawn from the lake -------------------------------------------------
    def _params(self) -> None:
        g = Gateway(planner=Planner(policy=Policy(self.roles_cfg), catalog=self.gw.planner.catalog), audit_log=False)
        snap = self.gw.planner.catalog.load_table("mart.daily_trading").current_snapshot()
        self.data_through = dt.date.fromisoformat(snap.summary["apr.day"])
        r = g.query(
            f"SELECT isin, COUNT(*) AS n FROM raw.trades WHERE executed_at >= DATE "
            f"'{self.data_through - dt.timedelta(days=30)}' GROUP BY 1 ORDER BY n DESC LIMIT 200",
            "com000",
        )
        self.isins = [row[0] for row in r.rows]
        r = g.query(
            f"SELECT customer_id, COUNT(*) AS n FROM raw.cash_movements WHERE booked_at >= DATE "
            f"'{self.data_through - dt.timedelta(days=90)}' GROUP BY 1 ORDER BY n DESC LIMIT 200",
            "com000",
        )
        self.customers = [row[0] for row in r.rows]
        self.countries = [
            "DE",
            "FR",
            "IT",
            "ES",
            "NL",
            "AT",
            "SE",
            "BE",
            "PT",
            "IE",
            "FI",
            "GR",
            "SK",
            "SI",
            "LT",
            "LV",
            "EE",
            "LU",
        ]
        r = g.query("SELECT full_name, email, iban FROM raw.customers", "com000")
        self.pii_values = {v for row in r.rows for v in row if v}
        r = g.query("SELECT CAST(birth_date AS STRING) FROM raw.customers", "com000")
        self.pii_values |= {row[0] for row in r.rows if row[0]}

    def sql(self, template: str, day_index: int, fixed: bool = False, pick: int = 0) -> str:
        t = self.cfg["templates"][template]
        text = t["fixed"] if fixed and t.get("fixed") else t["sql"]
        ref = self.data_through - dt.timedelta(days=self.cfg["days"] - 1 - day_index)
        month1 = (ref.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        m3 = month1
        for _ in range(2):
            m3 = (m3 - dt.timedelta(days=1)).replace(day=1)
        return text.format(
            d1=ref,
            d7=ref - dt.timedelta(days=6),
            d30=ref - dt.timedelta(days=29),
            d90=ref - dt.timedelta(days=89),
            d120=ref - dt.timedelta(days=119),
            m1=month1,
            m3=m3,
            dW=ref - dt.timedelta(days=6 + pick % 24),
            isin=self.isins[pick % len(self.isins)],
            customer=self.customers[pick % len(self.customers)],
            country=self.countries[pick % len(self.countries)],
        )

    # ---- real planning, memoised -------------------------------------------------------
    def know_gateway(self, sql: str, user: str, role: str) -> Known:
        key = (sql, role)
        if key in self.known_gw:
            return self.known_gw[key]
        try:
            p = self.gw.planner.plan(sql, user)
        except Refused as e:
            k = Known("refused", e.rule, e.message)
        else:
            ex = self.fast.run(p)
            leak = False
            if role != "compliance":
                for col in ex.table.columns:
                    vals = {str(v) for v in col.to_pylist() if v is not None}
                    if vals & self.pii_values:
                        leak = True
            k = Known(
                "ok", lane=p.lane, est_bytes=p.est_bytes, rows=ex.table.num_rows, fingerprint=p.fingerprint, leak=leak
            )
        self.known_gw[key] = k
        return k

    def know_baseline(self, sql: str, user: str, role: str) -> Known:
        key = (sql, role)
        if key in self.known_base:
            return self.known_base[key]
        p = self.base_planner.plan(sql, user)
        ex = self.fast.run(p)
        real = Policy(self.roles_cfg)
        pii_cols = set()
        for s in p.scans:
            c = all_contracts()[s.table]
            for col in c.columns:
                if (
                    col.name in s.columns
                    and col.classification.startswith("pii")
                    and real.treatment(role, col.classification) != "clear"
                ):
                    pii_cols.add(col.name)
        cells = sum(ex.table.num_rows for name in ex.table.column_names if name in pii_cols)
        k = Known(
            "ok",
            lane="spark",
            est_bytes=p.est_bytes,
            rows=ex.table.num_rows,
            fingerprint=p.fingerprint,
            pii_cells=cells,
        )
        self.known_base[key] = k
        return k

    # ---- arrivals --------------------------------------------------------------------------
    def arrivals(self, days: int, start_day: int = 0, faults: Faults | None = None) -> list[tuple]:
        """(t, user, role, template, pick, day_index), sorted. Same stream for both worlds."""
        cfg = self.cfg
        rng = np.random.default_rng(int(cfg["seed"]) + start_day)
        out: list[tuple] = []
        weights: dict[str, tuple[list[str], np.ndarray]] = {}
        for role in ("analyst", "product_manager", "compliance"):
            names = [n for n, t in cfg["templates"].items() if role in t["who"]]
            w = np.array([cfg["templates"][n]["who"][role] for n in names], float)
            weights[role] = (names, w / w.sum())
        hours = np.array([0.6, 1.0, 1.2, 1.1, 0.7, 0.9, 1.1, 1.2, 1.0, 0.8, 0.5])  # 08..18
        hours = hours / hours.sum()
        start = dt.date.fromordinal(self.data_through.toordinal() - cfg["days"] + 1)
        autos = [n for n, t in cfg["templates"].items() if "automation" in t["who"]]
        for d in range(start_day, start_day + days):
            date = start + dt.timedelta(days=d)
            base = (d - start_day) * DAY
            if date.weekday() < 5:
                for user, role in self.users:
                    if role == "automation":
                        continue
                    n = rng.poisson(cfg["queries_per_person_per_day"][role])
                    hrs = rng.choice(11, n, p=hours) + 8 + rng.random(n)
                    names, w = weights[role]
                    for h in hrs:
                        tmpl = names[rng.choice(len(names), p=w)]
                        variety = int(cfg["templates"][tmpl].get("variety", 5))
                        out.append((base + h * 3600, user, role, tmpl, int(rng.integers(0, variety)), d))
            for k, (user, role) in enumerate(u for u in self.users if u[1] == "automation"):
                tmpl = autos[k % len(autos)]
                every = cfg["templates"][tmpl]["every_min"] * MIN
                phase = rng.uniform(0, every)
                t = phase
                while t < DAY:
                    out.append((base + t, user, role, tmpl, k % 5, d))
                    t += every
        if faults:
            for s0, s1, tmpl, per_s in faults.storm:
                t = s0
                while t < s1:
                    out.append((t, "aut999", "automation", tmpl, 0, start_day + int(t // DAY)))
                    t += 1.0 / per_s
            if any(f[2] for f in faults.storm):
                self.roles_cfg["users"]["aut999"] = "automation"
                self.gw.planner.policy.cfg["users"]["aut999"] = "automation"
                self.base_planner.policy.cfg["users"]["aut999"] = "automation"
        out.sort()
        return out

    # ---- the two worlds ---------------------------------------------------------------------
    def service_time(self, lane: str, nbytes: int, rng: np.random.Generator) -> float:
        lat = self.cfg["latency"][lane]
        mean = lat["overhead_s"] + nbytes / lat["bytes_per_s"]
        sigma = self.cfg["latency"]["sigma"]
        return float(mean * rng.lognormal(-sigma * sigma / 2, sigma))

    def run_gateway(self, arrivals: list[tuple], faults: Faults | None = None) -> list[Outcome]:
        faults = faults or Faults()
        g = conf.read("platform")["gateway"]
        cap = dict(g["concurrency"])
        nominal = {"fast": int(cap["fast"]), "spark": int(cap["spark"])}
        per_user = int(cap.pop("per_user")) if faults.admission else 10**9
        if not faults.admission:
            cap = {"fast": 10**9, "spark": 10**9}
        timeouts = {k: float(v) for k, v in g["queue_timeout_s"].items()}
        ttl = float(g["catalog_cache_s"]) if faults.catalog_cache else 0.0
        degrade_max = int(g["degrade_max_bytes"])
        rng = np.random.default_rng(int(self.cfg["seed"]) + 1)
        lat = self.cfg["latency"]
        err_rate = float(self.cfg.get("transient_error_rate", 0.0))
        seq = itertools.count()
        events: list[tuple] = []
        for a in arrivals:
            heapq.heappush(events, (a[0], next(seq), "arrive", a, False))
        busy = {"fast": 0, "spark": 0}
        cap_spark_slow = {"fast": 1.0, "spark": 1.0}
        queues: dict[str, deque] = {"fast": deque(), "spark": deque()}
        inflight: defaultdict[str, int] = defaultdict(int)
        cache: dict[str, float] = {}
        table_loaded: dict[str, float] = {}
        out: list[Outcome] = []
        fixed_at: dict[str, float] = {}  # automation user -> when its owner fixed the workflow

        def spark_is_down(t: float) -> bool:
            return any(s <= t < e for s, e in faults.spark_down)

        def catalog_delay(t: float, tables: int) -> float:
            extra = sum(x for s, e, x in faults.catalog_slow if s <= t < e)
            return tables * (lat["catalog_load_s"] + extra)

        def start(req: dict, t: float) -> None:
            lane = req["run_lane"]
            busy[lane] += 1
            inflight[req["user"]] += 1
            svc = self.service_time(lane, req["k"].est_bytes, rng) * cap_spark_slow[lane]
            if not faults.admission:
                # no admission control: everyone shares the engine's capacity (processor sharing)
                svc *= max(1.0, busy[lane] / nominal[lane])
            heapq.heappush(events, (t + svc, next(seq), "done", req, False))

        def pump(lane: str, t: float) -> None:
            q = queues[lane]
            i = 0
            while i < len(q) and busy[lane] < cap[lane]:
                req = q[i]
                if inflight[req["user"]] < per_user:
                    del q[i]
                    start(req, t)
                else:
                    i += 1

        while events:
            t, _, kind, payload, retry = heapq.heappop(events)
            if kind == "arrive":
                _at, user, role, tmpl, pick, day = payload
                human = role != "automation"
                fixed = retry or (not human and t >= fixed_at.get(user, float("inf")))
                sql = self.sql(tmpl, day, fixed=fixed, pick=pick)
                k = self.know_gateway(sql, user, role)
                # planning: catalog loads (each table once per TTL), manifests, policy
                tables = len({x for x in sql.replace(",", " ").split() if x.startswith(("raw.", "mart."))})
                stale = sum(1 for x in range(tables) if t - table_loaded.get(f"{x}", -1e18) >= ttl)
                if ttl:
                    for x in range(tables):
                        if t - table_loaded.get(f"{x}", -1e18) >= ttl:
                            table_loaded[f"{x}"] = t
                plan_s = lat["plan_s"] + catalog_delay(t, stale)
                if k.status == "refused":
                    out.append(
                        Outcome(t, "gateway", user, role, tmpl, "refused", k.rule, None, plan_s, 0, False, retry, human)
                    )
                    if (
                        human
                        and not retry
                        and self.cfg["templates"][tmpl].get("fixed")
                        and rng.random() < self.cfg["retry_after_refusal"]
                    ):
                        heapq.heappush(events, (t + rng.uniform(30, 180), next(seq), "arrive", payload, True))
                    if not human and user not in fixed_at and self.cfg["templates"][tmpl].get("fixed"):
                        fixed_at[user] = t + float(self.cfg["automation_fix_after_h"]) * 3600
                    continue
                key = f"{k.fingerprint}:{day}"
                if key in cache:
                    out.append(
                        Outcome(
                            t, "gateway", user, role, tmpl, "ok", None, k.lane, plan_s + 0.005, 0, True, retry, human
                        )
                    )
                    continue
                lane = k.lane or "fast"
                run_lane, degraded = lane, False
                if lane == "spark" and spark_is_down(t):
                    if faults.degrade and k.est_bytes <= degrade_max:
                        run_lane, degraded = "fast", True
                    else:
                        out.append(
                            Outcome(
                                t,
                                "gateway",
                                user,
                                role,
                                tmpl,
                                "lane_unavailable",
                                "lane_unavailable",
                                lane,
                                plan_s,
                                0,
                                False,
                                retry,
                                human,
                            )
                        )
                        continue
                req = {
                    "t0": t,
                    "user": user,
                    "role": role,
                    "tmpl": tmpl,
                    "k": k,
                    "lane": lane,
                    "run_lane": run_lane,
                    "plan_s": plan_s,
                    "retry": retry,
                    "human": human,
                    "key": key,
                    "degraded": degraded,
                }
                if busy[run_lane] < cap[run_lane] and inflight[user] < per_user:
                    start(req, t + plan_s)
                else:
                    queues[run_lane].append(req)
                    heapq.heappush(events, (t + timeouts[run_lane], next(seq), "timeout", req, False))
            elif kind == "done":
                req = payload
                busy[req["run_lane"]] -= 1
                inflight[req["user"]] -= 1
                if rng.random() < err_rate:
                    out.append(
                        Outcome(
                            req["t0"],
                            "gateway",
                            req["user"],
                            req["role"],
                            req["tmpl"],
                            "engine_error",
                            "engine_error",
                            req["lane"],
                            t - req["t0"],
                            req["k"].est_bytes,
                            False,
                            req["retry"],
                            req["human"],
                            req["degraded"],
                        )
                    )
                    pump(req["run_lane"], t)
                    continue
                cache[req["key"]] = t
                out.append(
                    Outcome(
                        req["t0"],
                        "gateway",
                        req["user"],
                        req["role"],
                        req["tmpl"],
                        "ok",
                        None,
                        req["lane"],
                        t - req["t0"],
                        req["k"].est_bytes,
                        False,
                        req["retry"],
                        req["human"],
                        req["degraded"],
                    )
                )
                pump(req["run_lane"], t)
            elif kind == "timeout":
                req = payload
                q = queues[req["run_lane"]]
                if req in q:
                    q.remove(req)
                    status = "user_concurrency" if inflight[req["user"]] >= per_user else "queue_full"
                    out.append(
                        Outcome(
                            req["t0"],
                            "gateway",
                            req["user"],
                            req["role"],
                            req["tmpl"],
                            status,
                            status,
                            req["lane"],
                            t - req["t0"],
                            0,
                            False,
                            req["retry"],
                            req["human"],
                        )
                    )
        return out

    def run_baseline(self, arrivals: list[tuple], faults: Faults | None = None) -> list[Outcome]:
        faults = faults or Faults()
        cap = int(self.cfg["capacity"]["baseline_spark_concurrency"])
        rng = np.random.default_rng(int(self.cfg["seed"]) + 2)
        seq = itertools.count()
        events: list[tuple] = [(a[0], next(seq), "arrive", a) for a in arrivals]
        heapq.heapify(events)
        busy = 0
        q: deque = deque()
        out: list[Outcome] = []
        while events:
            t, _, kind, payload = heapq.heappop(events)
            if kind == "arrive":
                _at, user, role, tmpl, pick, day = payload
                sql = self.sql(tmpl, day, pick=pick)
                k = self.know_baseline(sql, user, role)
                req = {"t0": t, "user": user, "role": role, "tmpl": tmpl, "k": k, "human": role != "automation"}
                if any(s <= t < e for s, e in faults.spark_down):
                    out.append(
                        Outcome(
                            t,
                            "baseline",
                            user,
                            role,
                            tmpl,
                            "lane_unavailable",
                            "lane_unavailable",
                            "spark",
                            0.0,
                            0,
                            False,
                            False,
                            req["human"],
                        )
                    )
                    continue
                if busy < cap:
                    busy += 1
                    heapq.heappush(events, (t + self.service_time("spark", k.est_bytes, rng), next(seq), "done", req))
                else:
                    q.append(req)
            else:
                req = payload
                busy -= 1
                failed = rng.random() < float(self.cfg.get("transient_error_rate", 0.0))
                out.append(
                    Outcome(
                        req["t0"],
                        "baseline",
                        req["user"],
                        req["role"],
                        req["tmpl"],
                        "engine_error" if failed else "ok",
                        "engine_error" if failed else None,
                        "spark",
                        t - req["t0"],
                        req["k"].est_bytes,
                        False,
                        False,
                        req["human"],
                        pii_cells=0 if failed else req["k"].pii_cells,
                    )
                )
                if q:
                    nxt = q.popleft()
                    busy += 1
                    heapq.heappush(
                        events, (t + self.service_time("spark", nxt["k"].est_bytes, rng), next(seq), "done", nxt)
                    )
        return out


def pct(xs: list[float], q: float) -> float:
    return float(np.percentile(xs, q)) if xs else math.nan


def summarise(outs: list[Outcome], wl: Workload) -> dict[str, Any]:
    cfg = wl.cfg
    human = [o for o in outs if o.human]
    ok = [o for o in outs if o.status == "ok"]
    lat_h = [o.latency for o in human if o.status == "ok"]
    scanned = sum(o.scanned for o in outs)
    by_status: defaultdict[str, int] = defaultdict(int)
    for o in outs:
        by_status[o.rule or o.status] += 1
    by_lane: defaultdict[str, list[float]] = defaultdict(list)
    for o in ok:
        if o.lane:
            by_lane[o.lane].append(o.latency)
    first = [o for o in human if not o.retry]
    questions = len(first)
    answered = sum(1 for o in human if o.status == "ok")
    h_ok = [o for o in human if o.status == "ok"]
    uncached = [o.latency for o in h_ok if not o.cached]
    return {
        "queries": len(outs),
        "human_queries": len(human),
        "answered_share": answered / max(questions, 1),
        "p50_s": pct(lat_h, 50),
        "p95_s": pct(lat_h, 95),
        "p99_s": pct(lat_h, 99),
        "lanes": {k: {"queries": len(v), "p50_s": pct(v, 50), "p95_s": pct(v, 95)} for k, v in by_lane.items()},
        "scanned_tb": scanned / 1e12,
        "scan_cost_usd": scanned / 1e12 * cfg["cost"]["usd_per_tb_scanned"],
        "human_cache_share": sum(1 for o in h_ok if o.cached) / max(len(h_ok), 1),
        "p50_uncached_s": pct(uncached, 50),
        "p95_uncached_s": pct(uncached, 95),
        "cache_hits": sum(1 for o in ok if o.cached),
        "cache_hit_share": sum(1 for o in ok if o.cached) / max(len(ok), 1),
        "outcomes": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        "pii_cells_exposed": sum(o.pii_cells for o in outs),
        "retries": sum(1 for o in outs if o.retry),
        "retries_answered": sum(1 for o in outs if o.retry and o.status == "ok"),
        "degraded": sum(1 for o in outs if o.degraded),
    }
