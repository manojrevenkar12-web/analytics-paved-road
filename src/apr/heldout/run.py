"""Run a held-out set and grade it. `--first-run` records the first run, which is never rewritten.

ask:    each answerable question is parsed and run; it is correct when its result rows equal those
        of the expected metric query run the same way. A different answer is worse than a refusal
        and is counted separately.
access: each case is planned for its user; the decision, the rule and the masked columns must
        match.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from typing import Any

import yaml

from apr import conf
from apr.rollout.shadow import same_result

SETS = {"ask": "heldout/ask/questions.yaml", "access": "heldout/access/cases.yaml"}


def sha256(path: str) -> str:
    return hashlib.sha256(conf.path(path).read_bytes()).hexdigest()


def _expected_query(e: dict[str, Any]):
    from apr.semantic.layer import MetricQuery

    return MetricQuery.model_validate(
        {
            "metrics": e["metrics"],
            "dimensions": e.get("dimensions") or [],
            "grain": e.get("grain"),
            "start": e["start"],
            "end": e["end"],
            "filters": [{"dimension": k, "values": v} for k, v in (e.get("filters") or {}).items()],
            "order": e.get("order") or "natural",
            "limit": e.get("limit"),
        }
    )


def run_ask(data: dict[str, Any], gateway, parser: str = "sim") -> dict[str, Any]:
    from apr.ask.service import parse
    from apr.semantic import layer
    from apr.semantic.layer import Unanswerable

    ref = data["data_through"]
    if isinstance(ref, str):
        ref = dt.date.fromisoformat(ref)
    rows = []
    for c in data["questions"]:
        r: dict[str, Any] = {"id": c["id"], "question": c["question"], "answerable": c["answerable"]}
        try:
            q = parse(c["question"], ref, parser)
            layer.validate(q, ref)
            got = layer.run(q, "pia", gateway, ref)
            r["parsed"] = q.model_dump(mode="json")
            r["answered"] = got.refused is None
            if got.refused:
                r["reason"] = got.refused
        except Unanswerable as e:
            got, r["answered"], r["reason"] = None, False, e.reason
        except Exception as e:  # a crash is a failure of the parser, recorded as such
            got, r["answered"], r["reason"] = None, False, f"error: {type(e).__name__}: {e}"
        if c["answerable"]:
            exp = layer.run(_expected_query(c["expect"]), "pia", gateway, ref)
            r["expected_refused"] = exp.refused
            if not r["answered"]:
                r["grade"] = "refused_answerable"
            elif (
                got is not None
                and exp.refused is None
                and got.columns[-len(got.query.metrics) :] == exp.columns[-len(exp.query.metrics) :]
                and same_result(got.rows, exp.rows, rel=1e-6)
            ):
                r["grade"] = "correct"
            else:
                r["grade"] = "wrong"
        else:
            r["grade"] = "correct_refusal" if not r["answered"] else "answered_unanswerable"
        rows.append(r)
    grades: dict[str, int] = {}
    for r in rows:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    answerable = sum(1 for r in rows if r["answerable"])
    return {
        "set": "ask",
        "parser": parser,
        "grades": grades,
        "answerable": answerable,
        "accuracy": grades.get("correct", 0) / max(answerable, 1),
        "wrong_answers": grades.get("wrong", 0) + grades.get("answered_unanswerable", 0),
        "cases": rows,
    }


def run_access(data: dict[str, Any], planner) -> dict[str, Any]:
    from apr.gateway.planner import Refused

    rows = []
    for c in data["cases"]:
        r: dict[str, Any] = {
            "id": c["id"],
            "user": c["user"],
            "sql": c["sql"],
            "expect": c["expect"],
            "expected_rule": c.get("rule"),
            "expected_masked": sorted(c.get("masked") or []),
        }
        try:
            p = planner.plan(c["sql"], c["user"])
            r["got"], r["masked"] = "allow", p.masked
        except Refused as e:
            r["got"], r["rule"] = "refuse", e.rule
        except Exception as e:
            r["got"], r["rule"] = "error", f"{type(e).__name__}: {e}"
        if r["got"] != c["expect"]:
            r["grade"] = "wrong_decision"
        elif c["expect"] == "refuse" and r.get("rule") != c.get("rule"):
            r["grade"] = "wrong_rule"
        elif c["expect"] == "allow" and sorted(r.get("masked", [])) != r["expected_masked"]:
            r["grade"] = "wrong_masking"
        else:
            r["grade"] = "correct"
        rows.append(r)
    grades: dict[str, int] = {}
    for r in rows:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    leaks = [r["id"] for r in rows if r["expect"] == "refuse" and r["got"] == "allow"]
    over = [r["id"] for r in rows if r["expect"] == "allow" and r["got"] == "refuse"]
    return {
        "set": "access",
        "grades": grades,
        "accuracy": grades.get("correct", 0) / max(len(rows), 1),
        "allowed_but_should_refuse": leaks,
        "refused_but_should_allow": over,
        "cases": rows,
    }


def run_set(name: str, first_run: bool = False) -> dict[str, Any]:
    from apr.gateway.core import Gateway

    path = SETS[name]
    data = yaml.safe_load(conf.path(path).read_text())
    gw = Gateway(audit_log=False)
    res = run_ask(data, gw) if name == "ask" else run_access(data, gw.planner)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=conf.ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    res |= {
        "file": path,
        "sha256": sha256(path),
        "commit": commit,
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    out = conf.path("reports", "heldout", name)
    out.mkdir(parents=True, exist_ok=True)
    target = out / ("first_run.json" if first_run else "latest.json")
    if first_run and target.exists():
        raise SystemExit(f"{target} exists; the first run is never rewritten.")
    target.write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "cases"}, indent=2, default=str))
    return res
