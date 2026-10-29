"""Failure drills, each with a timeline written like a postmortem.

Simulated (one working day of the workload, fault injected, the real planner deciding):
spark_down, catalog_latency, query_storm. Real (a temporary lake, real ingestion, dbt and the
gateway): late_upstream, bad_batch. Alert times come from the same burn-rate arithmetic as the
generated Prometheus rules, and from the freshness rule for late data.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from apr import conf
from apr.slo.rules import burn_alerts, late_after_seconds
from apr.workload.report import slo_series
from apr.workload.sim import DAY, Faults, Outcome, Workload, pct


def _sec(hhmm: str) -> float:
    h, m = map(int, hhmm.split(":"))
    return h * 3600.0 + m * 60


def _clock(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}"


def _alerts(outs: list[Outcome], objective: str) -> list[dict[str, Any]]:
    cfg = conf.read("slo")
    g, t = slo_series(outs, 1)[objective]
    res = []
    for a in burn_alerts(list(g), list(t), cfg["objectives"][objective]["objective"], cfg):
        res.append(
            {
                "alert": a.name,
                "severity": a.severity,
                "start": _clock(a.start_min * 60),
                "end": _clock(a.end_min * 60) if a.end_min else None,
            }
        )
    return res


def _day_index(wl: Workload, day: dt.date) -> int:
    start = wl.data_through - dt.timedelta(days=wl.cfg["days"] - 1)
    return (day - start).days


def _humans(outs: list[Outcome], t0: float, t1: float) -> list[Outcome]:
    return [o for o in outs if o.human and t0 <= o.t < t1]


def _stats(outs: list[Outcome]) -> dict[str, Any]:
    ok = [o for o in outs if o.status == "ok"]
    return {
        "queries": len(outs),
        "answered": len(ok),
        "failed": sum(1 for o in outs if o.status in ("lane_unavailable", "queue_full", "engine_error")),
        "p50_s": round(pct([o.latency for o in ok], 50), 3),
        "p95_s": round(pct([o.latency for o in ok], 95), 3),
    }


# ---- simulated drills -------------------------------------------------------------------------


def spark_down(wl: Workload, cfg: dict[str, Any]) -> dict[str, Any]:
    d = cfg["spark_down"]
    s0, s1 = _sec(d["start"]), _sec(d["end"])
    di = _day_index(wl, cfg["day"])
    arr = wl.arrivals(1, start_day=di)
    with_degrade = wl.run_gateway(arr, Faults(spark_down=[(s0, s1)]))
    without = wl.run_gateway(arr, Faults(spark_down=[(s0, s1)], degrade=False))
    base = wl.run_baseline(arr, Faults(spark_down=[(s0, s1)]))
    win = (s0, s1)
    alerts = _alerts(with_degrade, "spark_lane_availability")
    alerts_nodeg = _alerts(without, "spark_lane_availability")
    overall = _alerts(with_degrade, "gateway_availability")
    degraded = sum(1 for o in with_degrade if o.degraded)
    st = _stats(_humans(with_degrade, *win))
    timeline = [
        (d["start"], "Kyuubi stops accepting sessions (the Spark lane is down)."),
        (
            d["start"],
            f"The gateway marks the lane down and runs queries up to "
            f"{conf.read('platform')['gateway']['degrade_max_bytes'] / 2**30:.0f} GiB on DuckDB "
            f"instead; larger ones get 503 with a retry hint.",
        ),
    ]
    for a in alerts:
        timeline.append((a["start"], f"Spark lane availability: {a['alert']} ({a['severity']}) fires."))
    timeline.append((d["end"], "Kyuubi is back; the lane is used again after its health check."))
    for a in alerts:
        if a["end"]:
            timeline.append((a["end"], f"Spark lane availability: {a['alert']} resolves."))
    return {
        "name": "spark_down",
        "kind": "simulated",
        "question": "If the Spark lane fails in working hours, who notices, how fast, and what still works?",
        "injected": f"Kyuubi unreachable {d['start']}-{d['end']} UTC on {cfg['day']}.",
        "timeline": sorted(timeline),
        "results": {
            "during_outage_people": st,
            "degraded_to_fast_lane": degraded,
            "failed_with_degrade": sum(1 for o in with_degrade if o.status == "lane_unavailable"),
            "failed_without_degrade": sum(1 for o in without if o.status == "lane_unavailable"),
            "failed_baseline": sum(1 for o in base if o.status == "lane_unavailable"),
            "alerts": alerts,
            "alerts_without_degrade": alerts_nodeg,
            "overall_availability_alerts": overall,
        },
        "verdict": _verdict(alerts, "page"),
    }


def catalog_latency(wl: Workload, cfg: dict[str, Any]) -> dict[str, Any]:
    d = cfg["catalog_latency"]
    s0, s1 = _sec(d["start"]), _sec(d["end"])
    di = _day_index(wl, cfg["day"])
    arr = wl.arrivals(1, start_day=di)
    cached = wl.run_gateway(arr, Faults(catalog_slow=[(s0, s1, d["extra_s"])]))
    uncached = wl.run_gateway(arr, Faults(catalog_slow=[(s0, s1, d["extra_s"])], catalog_cache=False))
    a_c, a_u = _alerts(cached, "fast_lane_latency"), _alerts(uncached, "fast_lane_latency")
    fast = lambda outs: [o for o in _humans(outs, s0, s1) if o.lane == "fast" and o.status == "ok"]  # noqa: E731
    within = lambda outs: float(np.mean([o.latency <= 2.0 for o in fast(outs)])) if fast(outs) else None  # noqa: E731
    timeline = [(d["start"], f"Iceberg REST catalog responses slow down by {d['extra_s']} s per table load.")]
    for a in a_c:
        timeline.append((a["start"], f"{a['alert']} ({a['severity']}) fires."))
    timeline.append((d["end"], "Catalog latency back to normal."))
    return {
        "name": "catalog_latency",
        "kind": "simulated",
        "question": "Does a slow catalog make every query slow, and does the latency SLO see it?",
        "injected": f"+{d['extra_s']} s per catalog load {d['start']}-{d['end']} UTC.",
        "timeline": sorted(timeline),
        "results": {
            "fast_within_2s_with_metadata_cache": within(cached),
            "fast_within_2s_without_cache": within(uncached),
            "p95_fast_with_cache_s": round(pct([o.latency for o in fast(cached)], 95), 3),
            "p95_fast_without_cache_s": round(pct([o.latency for o in fast(uncached)], 95), 3),
            "alerts_with_cache": a_c,
            "alerts_without_cache": a_u,
        },
        "verdict": "pass"
        if not any(a["severity"] == "page" for a in a_c) and any(a["severity"] == "page" for a in a_u)
        else "review",
    }


def query_storm(wl: Workload, cfg: dict[str, Any]) -> dict[str, Any]:
    d = cfg["query_storm"]
    s0, s1 = _sec(d["start"]), _sec(d["end"])
    di = _day_index(wl, cfg["day"])
    faults = Faults(storm=[(s0, s1, d["template"], d["per_second"])])
    arr = wl.arrivals(1, start_day=di, faults=faults)
    arr = [(a[0], a[1], a[2], a[3], (i if a[1] == "aut999" else a[4]), a[5]) for i, a in enumerate(arr)]
    calm_arr = wl.arrivals(1, start_day=di)
    calm = wl.run_gateway(calm_arr)
    guarded = wl.run_gateway(arr, faults)
    unguarded = wl.run_gateway(arr, Faults(storm=faults.storm, admission=False))
    others = lambda outs: [o for o in outs if o.user != "aut999" and s0 <= o.t < s1 + 300 and o.human]  # noqa: E731
    storm = [o for o in guarded if o.user == "aut999"]
    timeline = [
        (d["start"], f"An n8n workflow loops: {d['per_second']} distinct queries a second."),
        (
            d["start"],
            "Admission control holds it to its 2 concurrent queries; the rest wait up to 5 s and are refused with 429.",
        ),
        (d["end"], "The workflow is stopped."),
    ]
    return {
        "name": "query_storm",
        "kind": "simulated",
        "question": "Can one runaway automation make the platform slow for everyone else?",
        "injected": f"{d['per_second']} queries/s from one service user {d['start']}-{d['end']} UTC.",
        "timeline": timeline,
        "results": {
            "storm_queries": len(storm),
            "storm_refused_429": sum(1 for o in storm if o.status in ("user_concurrency", "queue_full")),
            "others_without_storm": _stats(others(calm)),
            "others_with_admission": _stats(others(guarded)),
            "others_without_admission": _stats(others(unguarded)),
        },
        # others must not notice the storm: p95 within 10% (+50 ms) of the same window without it
        "verdict": "pass" if _stats(others(guarded))["p95_s"] <= 1.1 * _stats(others(calm))["p95_s"] + 0.05 else "fail",
    }


# ---- real drills on a temporary lake -------------------------------------------------------------


@contextmanager
def temp_lake():
    from apr.lake import catalog as lake
    from apr.lake.ingest import ingest

    d = tempfile.mkdtemp(prefix="apr-drill-")
    old = os.environ.get("APR_WAREHOUSE")
    os.environ["APR_WAREHOUSE"] = d
    os.environ["APR_AUDIT_LOG"] = os.path.join(d, "audit.jsonl")
    lake.reset_cache()
    try:
        ingest(3)
        yield d
    finally:
        if old is None:
            os.environ.pop("APR_WAREHOUSE", None)
        else:
            os.environ["APR_WAREHOUSE"] = old
        os.environ.pop("APR_AUDIT_LOG", None)
        lake.reset_cache()
        shutil.rmtree(d, ignore_errors=True)


def late_upstream(cfg: dict[str, Any]) -> dict[str, Any]:
    from apr.contracts.model import all_contracts, by_name
    from apr.lake.catalog import load
    from apr.lake.ingest import ingest_day
    from apr.lake.publish import dbt, publish
    from apr.world.generate import World

    d = cfg["late_upstream"]
    w = World()
    with temp_lake():
        assert dbt("build")
        publish(w.date(2))
        day = w.date(3)
        runs = ingest_day(w, 3, delay_min={d["table"]: d["delay_min"]})
        landed = {r.table: dt.datetime.fromisoformat(r.landed_at) for r in runs}
        # the mart is built when its last upstream has landed (the DAG's sensors)
        mart = by_name("daily_trading")
        ups = [all_contracts()[t].table for t in all_contracts() if all_contracts()[t].name in mart.upstream]
        build_at = max(landed[t] for t in ups) + dt.timedelta(minutes=12)
        assert dbt("build")
        pub = publish(day)
        snap = load(mart.table).current_snapshot()
        through = snap.summary["apr.day"] if snap and snap.summary else None
        midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
        events = []
        for c in [all_contracts()[d["table"]], mart]:
            due = midnight + dt.timedelta(seconds=late_after_seconds(c.freshness.expected_by) - 86400)
            done = landed[c.table] if c.layer == "raw" else build_at
            if done > due:
                fire = due + dt.timedelta(minutes=10)
                sev = "page" if c.layer == "mart" else "ticket"
                events.append(
                    (
                        fire.strftime("%H:%M"),
                        f"APR_ProductLate ({sev}) for {c.name} to {c.owner.contact}: "
                        f"no data for {day} after {c.freshness.expected_by}.",
                    )
                )
                events.append((done.strftime("%H:%M"), f"{c.name} published for {day}; the alert resolves."))
        events.append(
            (landed[d["table"]].strftime("%H:%M"), f"{d['table']} for {day} lands, {d['delay_min']} minutes late.")
        )
        events.append((build_at.strftime("%H:%M"), "The daily_trading DAG's sensor releases; dbt builds the mart."))
        events.append(("04:30", "The other raw tables land on time."))
        late_by = (build_at - (midnight + dt.timedelta(hours=7))).total_seconds() / 60
        return {
            "name": "late_upstream",
            "kind": "real",
            "question": "When an upstream batch is late, who is told, and what do consumers see meanwhile?",
            "injected": f"{d['table']} for {day} lands {d['delay_min']} minutes after its usual 04:30.",
            "timeline": sorted(events),
            "results": {
                "mart_published": [r.status for r in pub],
                "mart_data_through": through,
                "mart_late_minutes": round(late_by),
                "landed": {k: v.isoformat() for k, v in landed.items()},
            },
            "verdict": "pass" if through == day.isoformat() and any("page" in e[1] for e in events) else "fail",
        }


def bad_batch(cfg: dict[str, Any]) -> dict[str, Any]:
    from apr.gateway.core import Gateway
    from apr.gateway.planner import Planner
    from apr.lake.catalog import catalog
    from apr.lake.ingest import ingest_day
    from apr.world.generate import World

    d = cfg["bad_batch"]
    w = World()
    with temp_lake():
        before = _snapshot_id("raw.trades")
        good = w.trades_on(3)
        amounts = good.column("amount_eur").to_numpy().copy()
        rng = np.random.default_rng(3)
        idx = rng.choice(len(amounts), d["negative_amounts"], replace=False)
        amounts[idx] = -amounts[idx]
        ids = good.column("trade_id").to_numpy(zero_copy_only=False).copy()
        dup = rng.choice(len(ids) - 1, d["duplicate_ids"], replace=False)
        ids[dup + 1] = ids[dup]
        bad = good.set_column(good.schema.get_field_index("amount_eur"), "amount_eur", pa.array(amounts))
        bad = bad.set_column(bad.schema.get_field_index("trade_id"), "trade_id", pa.array(ids))
        runs = ingest_day(w, 3, tamper={"raw.trades": bad})
        run = next(r for r in runs if r.table == "raw.trades")
        after = _snapshot_id("raw.trades")
        gw = Gateway(planner=Planner(catalog=catalog()), audit_log=False)
        q = f"SELECT COUNT(*) FROM raw.trades WHERE executed_at >= DATE '{w.date(3)}'"
        seen = gw.query(q, "ana").rows[0][0]
        fixed = ingest_day(w, 3)
        run2 = next(r for r in fixed if r.table == "raw.trades")
        gw.planner.forget()  # metadata is cached for catalog_cache_s; the drill does not wait
        seen2 = gw.query(q, "ana").rows[0][0]
        failed = [f"{c.check} on {c.column}: {c.detail}" for c in run.checks if not c.ok]
        _ = pc
        return {
            "name": "bad_batch",
            "kind": "real",
            "question": "If a source sends a corrupt batch, does anyone read it before it is caught?",
            "injected": f"raw.trades for {w.date(3)} with {d['negative_amounts']} negative amounts and "
            f"{d['duplicate_ids']} duplicated trade ids.",
            "timeline": [
                ("04:30", "The batch is written to the audit branch audit-" + w.date(3).isoformat() + "."),
                ("04:31", "Contract checks fail: " + "; ".join(failed) + "."),
                (
                    "04:31",
                    "main is not moved; the run is quarantined and the owner (#brokerage-data) "
                    "is told. Readers keep the previous day's data.",
                ),
                ("05:20", "The source re-sends a clean batch; it passes the checks and is published."),
            ],
            "results": {
                "first_run": run.status,
                "failed_checks": failed,
                "main_unchanged": before == after,
                "rows_visible_while_quarantined": seen,
                "second_run": run2.status,
                "rows_visible_after_fix": seen2,
            },
            "verdict": "pass"
            if run.status == "quarantined"
            and before == after
            and seen == 0
            and run2.status == "published"
            and seen2 > 0
            else "fail",
        }


def _snapshot_id(table: str) -> int | None:
    from apr.lake.catalog import load

    snap = load(table).current_snapshot()
    return snap.snapshot_id if snap else None


def _verdict(alerts: list[dict[str, Any]], want: str) -> str:
    return "pass" if any(a["severity"] == want for a in alerts) else "fail"


def markdown(d: dict[str, Any]) -> str:
    lines = [
        f"# Drill: {d['name']} ({d['kind']})",
        "",
        f"**Question.** {d['question']}",
        "",
        f"**Injected.** {d['injected']}",
        "",
        "| Time (UTC) | What happened |",
        "|---|---|",
    ]
    lines += [f"| {t} | {e} |" for t, e in d["timeline"]]
    lines += [
        "",
        "**Results**",
        "",
        "```json",
        json.dumps(d["results"], indent=2, default=str),
        "```",
        "",
        f"**Verdict:** {d['verdict']}",
        "",
    ]
    return "\n".join(lines)


def run_all(only: list[str] | None = None) -> list[dict[str, Any]]:
    cfg = dict(conf.read("drills"))
    if isinstance(cfg["day"], str):
        cfg["day"] = dt.date.fromisoformat(cfg["day"])
    out = []
    names = only or ["spark_down", "catalog_latency", "query_storm", "late_upstream", "bad_batch"]
    wl = Workload() if set(names) & {"spark_down", "catalog_latency", "query_storm"} else None
    for n in names:
        fn = globals()[n]
        res = fn(wl, cfg) if n in ("spark_down", "catalog_latency", "query_storm") else fn(cfg)
        out.append(res)
        print(f"{n}: {res['verdict']}")
    rdir = conf.path("reports", "drills")
    rdir.mkdir(parents=True, exist_ok=True)
    for d in out:
        (rdir / f"{d['name']}.md").write_text(markdown(d))
    existing = {}
    p = rdir / "drills.json"
    if p.exists():
        existing = {d["name"]: d for d in json.loads(p.read_text())["drills"]}
    existing.update({d["name"]: d for d in out})
    p.write_text(
        json.dumps({"generated_by": "apr drill", "drills": list(existing.values())}, indent=2, default=str) + "\n"
    )
    _ = DAY
    return out
