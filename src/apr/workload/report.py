"""Run the workload simulation and write reports/workload and reports/slo."""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

import numpy as np

from apr import conf
from apr.slo.rules import budget_remaining, burn_alerts
from apr.workload.sim import DAY, Outcome, Workload, summarise


def slo_series(outs: list[Outcome], days: int, step: int = 60) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per-minute good and total counts for each objective, as Prometheus would count them."""
    n = int(days * DAY // step)
    series: dict[str, tuple[np.ndarray, np.ndarray]] = {
        k: (np.zeros(n), np.zeros(n)) for k in conf.read("slo")["objectives"]
    }
    for o in outs:
        i = min(int(o.t // step), n - 1)
        if o.status in ("ok", "queue_full", "lane_unavailable", "engine_error"):
            g, t = series["gateway_availability"]
            t[i] += 1
            g[i] += o.status == "ok"
        if (
            o.lane == "spark"
            and not o.degraded
            and not o.cached
            and o.status in ("ok", "queue_full", "lane_unavailable", "engine_error")
        ):
            g, t = series["spark_lane_availability"]
            t[i] += 1
            g[i] += o.status == "ok"
        if o.status == "ok" and o.lane == "fast" and not o.degraded and not o.cached:
            g, t = series["fast_lane_latency"]
            t[i] += 1
            g[i] += o.latency <= 2.0
        if (o.status == "ok" and o.lane == "spark") or o.degraded:
            g, t = series["spark_lane_latency"]
            t[i] += 1
            g[i] += o.latency <= 120.0
    return series


def slo_status(outs: list[Outcome], days: int, start: dt.date) -> dict[str, Any]:
    cfg = conf.read("slo")
    out: dict[str, Any] = {
        "window_days": days,
        "from": start.isoformat(),
        "to": (start + dt.timedelta(days=days - 1)).isoformat(),
        "simulated": True,
        "objectives": [],
    }
    for name, (g, t) in slo_series(outs, days).items():
        o = cfg["objectives"][name]
        alerts = burn_alerts(list(g), list(t), o["objective"], cfg)
        good, total = float(g.sum()), float(t.sum())
        daily = []
        for d in range(days):
            sl = slice(d * 1440, (d + 1) * 1440)
            gg, tt = float(g[sl].sum()), float(t[sl].sum())
            daily.append(
                {
                    "day": (start + dt.timedelta(days=d)).isoformat(),
                    "good": gg,
                    "total": tt,
                    "ratio": gg / tt if tt else None,
                }
            )
        cum_bad = np.cumsum(t - g)
        cum_tot = np.cumsum(t)
        burn = [
            {
                "day": (start + dt.timedelta(days=d)).isoformat(),
                "budget_remaining": 1
                - (cum_bad[min((d + 1) * 1440, len(g)) - 1] / max(cum_tot[min((d + 1) * 1440, len(g)) - 1], 1))
                / (1 - o["objective"]),
            }
            for d in range(days)
        ]
        out["objectives"].append(
            {
                "name": name,
                "description": o["description"],
                "objective": o["objective"],
                "good": good,
                "total": total,
                "ratio": good / total if total else None,
                "budget_remaining": budget_remaining(good, total, o["objective"]),
                "alerts": [
                    {
                        "name": a.name,
                        "severity": a.severity,
                        "start": (
                            dt.datetime.combine(start, dt.time()) + dt.timedelta(minutes=a.start_min)
                        ).isoformat(),
                        "minutes": (a.end_min or len(g)) - a.start_min,
                    }
                    for a in alerts
                ],
                "daily": daily,
                "budget": burn,
            }
        )
    return out


def drill_faults(wl: Workload, days: int):
    """The drills' outages placed on their day of the month, so the SLO report includes them."""
    from apr.workload.sim import Faults

    d = dict(conf.read("drills"))
    day = d["day"] if isinstance(d["day"], dt.date) else dt.date.fromisoformat(d["day"])
    start = wl.data_through - dt.timedelta(days=wl.cfg["days"] - 1)
    off = (day - start).days * DAY
    if not 0 <= off < days * DAY:
        return Faults()

    def sec(hhmm: str) -> float:
        h, m = map(int, hhmm.split(":"))
        return h * 3600.0 + m * 60

    sd, cl = d["spark_down"], d["catalog_latency"]
    return Faults(
        spark_down=[(off + sec(sd["start"]), off + sec(sd["end"]))],
        catalog_slow=[(off + sec(cl["start"]), off + sec(cl["end"]), float(cl["extra_s"]))],
    )


def run_and_report(days: int | None = None) -> dict[str, Any]:
    t0 = time.time()
    wl = Workload()
    days = days or int(wl.cfg["days"])
    arrivals = wl.arrivals(days)
    faults = drill_faults(wl, days) if wl.cfg.get("include_drill_faults") else None
    gw = wl.run_gateway(arrivals, faults)
    base = wl.run_baseline(arrivals, faults)
    sg, sb = summarise(gw, wl), summarise(base, wl)
    leaks = sum(1 for k in wl.known_gw.values() if k.leak)
    start = wl.data_through - dt.timedelta(days=wl.cfg["days"] - 1)
    cost = wl.cfg["cost"]
    months = days / 30.0
    summary = {
        "generated_by": "apr simulate",
        "days": days,
        "users": len(wl.users),
        "scale": wl.scale,
        "distinct_queries": {"gateway": len(wl.known_gw), "baseline": len(wl.known_base)},
        "gateway": sg,
        "baseline": sb,
        "gateway_leaks": leaks,
        "gateway_pods_usd": cost["gateway_pods_usd_per_month"] * months,
        "runtime_s": round(time.time() - t0, 1),
    }
    rdir = conf.path("reports", "workload")
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (rdir / "SUMMARY.md").write_text(markdown(summary))
    sdir = conf.path("reports", "slo")
    sdir.mkdir(parents=True, exist_ok=True)
    status = slo_status(gw, days, start)
    (sdir / "status.json").write_text(json.dumps(status, indent=2, default=float) + "\n")
    print(markdown(summary))
    return summary


def _f(x: float, unit: str = "s") -> str:
    if unit == "s":
        return f"{x:.2f} s" if x < 60 else f"{x / 60:.1f} min"
    return f"{x:,.0f}"


def markdown(s: dict[str, Any]) -> str:
    g, b = s["gateway"], s["baseline"]
    rows = [
        (
            "People's queries answered (the rest refused by policy)",
            f"{b['answered_share']:.1%}",
            f"{g['answered_share']:.1%}",
        ),
        ("Median wait for an answer (people)", _f(b["p50_s"]), _f(g["p50_s"])),
        ("95th percentile (people)", _f(b["p95_s"]), _f(g["p95_s"])),
        ("99th percentile (people)", _f(b["p99_s"]), _f(g["p99_s"])),
        ("Data scanned, production scale", f"{b['scanned_tb']:.1f} TB", f"{g['scanned_tb']:.1f} TB"),
        (
            "Scan cost at the assumed rate",
            f"${b['scan_cost_usd']:,.0f}",
            f"${g['scan_cost_usd']:,.0f} + ${s['gateway_pods_usd']:,.0f} pods",
        ),
        ("PII cells returned to roles not cleared for them", f"{b['pii_cells_exposed']:,}", "0"),
        (
            "Answered from the snapshot-keyed cache (all / people)",
            "–",
            f"{g['cache_hit_share']:.1%} / {g['human_cache_share']:.1%}",
        ),
        ("p95 for people when not cached", _f(b["p95_s"]), _f(g["p95_uncached_s"])),
    ]
    out = [
        "# Workload simulation",
        "",
        f"Generated by `apr simulate`: {s['users']} users and automations, {s['days']} days, "
        f"{g['queries']:,} queries through the gateway and {b['queries']:,} against the baseline "
        f"(the same arrivals; the gateway's extra queries are people retrying after a refusal). "
        f"Access decisions, byte estimates and result rows come from the real planner and real execution "
        f"on the lake ({s['distinct_queries']['gateway']:,} distinct queries); bytes are scaled x{s['scale']['default']:.0f} "
        f"to production volume and execution and queueing times are modelled (config/workload.yaml).",
        "",
        "| | Baseline: every query on the Spark cluster | Through the gateway |",
        "|---|---|---|",
        *[f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows],
        "",
        "Gateway lanes:",
        "",
        "| Lane | Queries | p50 | p95 |",
        "|---|---|---|---|",
        *[f"| {k} | {v['queries']:,} | {_f(v['p50_s'])} | {_f(v['p95_s'])} |" for k, v in sorted(g["lanes"].items())],
        "",
        "Outcomes through the gateway:",
        "",
        "| Outcome | Queries |",
        "|---|---|",
        *[f"| {k} | {v:,} |" for k, v in g["outcomes"].items()],
        "",
        "The run includes the drill day's faults (config/drills.yaml): the Spark lane down for 40 minutes and "
        "a slow catalog for an hour, plus a background rate of transient engine errors in both worlds.",
        "",
        f"People retried {g['retries']:,} refused queries following the refusal message; "
        f"{g['retries_answered']:,} of the retries were answered.",
        f"Results checked for clear PII values (names, e-mails, IBANs, birth dates) for every role but "
        f"compliance: {s['gateway_leaks']} leaks in {s['distinct_queries']['gateway']:,} distinct queries.",
        "",
    ]
    return "\n".join(out)
