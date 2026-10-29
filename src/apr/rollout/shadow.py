"""Shadow replays for platform changes, run before a rollout.

Policy shadow: replay the audit log under a candidate roles.yaml and list every decision that
would change. Newly refused queries break someone's work; newly allowed or unmasked ones widen
access and need the data protection officer's review.

Engine shadow: replay audited queries on a candidate lane (a new DuckDB or Spark version, the
Spark lane against the fast lane) and compare results, with a tolerance for floating-point
summation order, and latency.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from typing import Any

import yaml

from apr import conf
from apr.gateway import audit
from apr.gateway.planner import Planner, Policy, Refused


def decide(planner: Planner, sql: str, user: str) -> dict[str, Any]:
    try:
        p = planner.plan(sql, user)
        return {"status": "ok", "masked": p.masked, "lane": p.lane}
    except Refused as e:
        return {"status": "refused", "rule": e.rule}


def shadow_policy(
    candidate: str | dict, entries: list[dict] | None = None, limit: int | None = None, catalog=None
) -> dict[str, Any]:
    if isinstance(candidate, str):
        with open(candidate, encoding="utf-8") as f:
            cand = yaml.safe_load(f)
    else:
        cand = candidate
    current, nxt = Planner(Policy(), catalog=catalog), Planner(Policy(cand), catalog=catalog)
    entries = entries if entries is not None else audit.read(limit)
    seen: set[tuple[str, str]] = set()
    changes = []
    kinds: Counter[str] = Counter()
    for e in entries:
        key = (e["user"], e["sql"])
        if key in seen or not e.get("sql"):
            continue
        seen.add(key)
        a, b = decide(current, e["sql"], e["user"]), decide(nxt, e["sql"], e["user"])
        if a == b or (a["status"] == b["status"] == "ok" and a["masked"] == b["masked"]):
            continue
        if a["status"] == "ok" and b["status"] == "refused":
            kind = "newly_refused"
        elif a["status"] == "refused" and b["status"] == "ok":
            kind = "newly_allowed"
        elif a["status"] == b["status"] == "ok":
            kind = "unmasked" if len(b["masked"]) < len(a["masked"]) else "newly_masked"
        else:
            kind = "rule_changed"
        kinds[kind] += 1
        changes.append({"kind": kind, "user": e["user"], "sql": e["sql"], "now": a, "candidate": b})
    needs_review = kinds["newly_allowed"] + kinds["unmasked"]
    return {
        "summary": {
            "replayed": len(seen),
            "changed": len(changes),
            **kinds,
            "needs_dpo_review": needs_review > 0,
            "breaks_users": kinds["newly_refused"] > 0,
        },
        "changes": changes,
    }


def same_result(a: list[list[Any]], b: list[list[Any]], rel: float = 1e-9) -> bool:
    def norm(rows):
        return sorted(
            (tuple("" if v is None else (round(v, 6) if isinstance(v, float) else v) for v in r) for r in rows),
            key=lambda t: tuple(str(x) for x in t),
        )

    ra, rb = norm(a), norm(b)
    if len(ra) != len(rb):
        return False
    for x, y in zip(ra, rb):
        for u, v in zip(x, y):
            if isinstance(u, float) and isinstance(v, float):
                if not math.isclose(u, v, rel_tol=rel, abs_tol=1e-6):
                    return False
            elif str(u) != str(v):
                return False
    return True


def shadow_engine(gateway, run_candidate, entries: list[dict] | None = None, limit: int = 200) -> dict[str, Any]:
    """`run_candidate(plan) -> (columns, rows)`; replays audited, answered queries on it."""
    from apr.gateway.core import to_rows

    entries = [e for e in (entries if entries is not None else audit.read()) if e.get("status") == "ok"][-limit:]
    out = []
    for e in entries:
        p = gateway.plan(e["sql"], e["user"])
        t0 = time.perf_counter()
        base = to_rows(gateway.fast.run(p).table)
        t1 = time.perf_counter()
        cand = run_candidate(p)
        t2 = time.perf_counter()
        out.append(
            {
                "sql": e["sql"],
                "user": e["user"],
                "same": same_result(base[1], cand[1]),
                "base_s": round(t1 - t0, 4),
                "candidate_s": round(t2 - t1, 4),
            }
        )
    diffs = [o for o in out if not o["same"]]
    return {"summary": {"replayed": len(out), "different": len(diffs)}, "diffs": diffs, "results": out}


def write_report(rep: dict[str, Any], name: str) -> str:
    d = conf.path("reports", "rollout")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(rep, indent=2, default=str) + "\n")
    return str(p)
