"""apr: the paved road's command line."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import warnings

warnings.filterwarnings("ignore", message="Delete operation did not match any records")


def cmd_build(a: argparse.Namespace) -> int:
    from apr.contracts.generate import write_dbt_schema
    from apr.lake.catalog import ensure_all
    from apr.lake.ingest import ingest
    from apr.lake.publish import dbt, publish
    from apr.world.generate import World

    write_dbt_schema()
    ensure_all()
    runs = ingest(a.days)
    bad = [r for r in runs if r.status != "published"]
    print(f"ingested {len(runs)} batches, {len(bad)} quarantined")
    if not dbt("build"):
        print("dbt build failed", file=sys.stderr)
        return 1
    w = World()
    last = w.date((a.days or w.days) - 1)
    for r in publish(last):
        print(f"{r.table}: {r.status}, {r.rows} rows, data through {last}")
    cmd_maintain(a)
    return 0


def cmd_maintain(a: argparse.Namespace) -> int:
    from apr.lake.maintain import maintain

    for m in maintain():
        if m.manifests_before != m.manifests_after or m.snapshots_expired:
            print(
                f"{m.table}: manifests {m.manifests_before} -> {m.manifests_after}, "
                f"{m.snapshots_expired} snapshots expired"
            )
    return 0


def cmd_ingest(a: argparse.Namespace) -> int:
    """One table's batch for one business day (what an Airflow ingest task runs)."""
    from apr.contracts.model import all_contracts
    from apr.lake.ingest import write
    from apr.world.generate import World

    w = World()
    day = dt.date.fromisoformat(a.day)
    i = (day - w.start).days
    c = all_contracts()[a.table]
    run = write(c, w.day(i)[a.table], day)
    print(
        json.dumps(
            {
                "table": run.table,
                "day": run.day,
                "status": run.status,
                "rows": run.rows,
                "failed": [f"{x.check} {x.column}: {x.detail}" for x in run.checks if not x.ok],
            }
        )
    )
    return 0 if run.status == "published" else 4


def cmd_publish(a: argparse.Namespace) -> int:
    """Publish the dbt-built marts to Iceberg with write-audit-publish (after `dbt build`)."""
    from apr.lake.publish import publish

    bad = 0
    for r in publish(dt.date.fromisoformat(a.day)):
        if a.model and r.table != f"mart.{a.model}":
            continue
        print(f"{r.table}: {r.status}, {r.rows} rows")
        bad += r.status != "published"
    return 4 if bad else 0


def cmd_dbt(a: argparse.Namespace) -> int:
    from apr.lake.publish import dbt

    return 0 if dbt(*a.args) else 1


def cmd_generate(a: argparse.Namespace) -> int:
    from apr.contracts.generate import write_dbt_schema, write_grafana
    from apr.semantic.docs import write_metrics_doc
    from apr.slo.promtest import write_tests
    from apr.slo.rules import write_rules

    for p in (write_dbt_schema(), write_grafana(), write_rules(), write_tests(), write_metrics_doc()):
        print(p)
    return 0


def cmd_lint(a: argparse.Namespace) -> int:
    from apr.contracts.lint import lint

    findings = lint(base=a.base)
    for f in findings:
        print(f"{f.level}: {f.product}: {f.rule}: {f.message}")
    errors = [f for f in findings if f.level == "error"]
    print(f"{len(errors)} errors, {len(findings) - len(errors)} warnings")
    return 1 if errors else 0


def cmd_new(a: argparse.Namespace) -> int:
    from apr.contracts.scaffold import scaffold

    for p in scaffold(a.name, a.domain, a.team, a.upstream or []):
        print(f"created {p}")
    return 0


def cmd_query(a: argparse.Namespace) -> int:
    from apr.gateway.core import Gateway

    r = Gateway().query(a.sql, a.user)
    print(
        json.dumps({k: v for k, v in r.to_dict().items() if k != "rows"} | {"rows": r.rows[:20]}, indent=2, default=str)
    )
    return 0 if r.status == "ok" else 2


def cmd_ask(a: argparse.Namespace) -> int:
    from apr.ask.service import ask
    from apr.gateway.core import Gateway

    ans = ask(a.question, a.user, Gateway(), parser=a.parser)
    print(ans.text)
    if a.verbose:
        print(json.dumps(ans.query, indent=2, default=str))
        for s in ans.sql:
            print(s)
    return 0 if ans.answered else 2


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    from apr.api.app import create_app

    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="info")
    return 0


def cmd_simulate(a: argparse.Namespace) -> int:
    from apr.workload.report import run_and_report

    run_and_report(days=a.days)
    return 0


def cmd_drill(a: argparse.Namespace) -> int:
    from apr.drills.run import run_all

    run_all(only=a.only)
    return 0


def cmd_budget(a: argparse.Namespace) -> int:
    from apr.slo.budget import check

    ok, text = check()
    print(text)
    return 0 if ok else 3


def cmd_shadow(a: argparse.Namespace) -> int:
    from apr.rollout.shadow import shadow_policy

    rep = shadow_policy(a.roles, limit=a.limit)
    print(json.dumps(rep["summary"], indent=2))
    return 0


def cmd_heldout(a: argparse.Namespace) -> int:
    from apr.heldout.run import run_set

    run_set(a.set, first_run=a.first_run)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apr", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="ingest the simulated brokerage, run dbt, publish the marts")
    b.add_argument("--days", type=int, default=None)
    b.set_defaults(fn=cmd_build)
    ig = sub.add_parser("ingest", help="load one table's batch for one day (write-audit-publish)")
    ig.add_argument("--table", required=True)
    ig.add_argument("--day", required=True)
    ig.set_defaults(fn=cmd_ingest)
    pb = sub.add_parser("publish", help="publish dbt-built marts to Iceberg (write-audit-publish)")
    pb.add_argument("--day", required=True)
    pb.add_argument("--model")
    pb.set_defaults(fn=cmd_publish)
    db = sub.add_parser("dbt", help="run dbt with the lake's profile")
    db.add_argument("args", nargs=argparse.REMAINDER)
    db.set_defaults(fn=cmd_dbt)
    sub.add_parser("maintain", help="compact tables and expire old snapshots").set_defaults(fn=cmd_maintain)
    sub.add_parser("generate", help="regenerate dbt tests, dashboard and alert rules from the contracts").set_defaults(
        fn=cmd_generate
    )
    li = sub.add_parser("lint", help="check the data product contracts")
    li.add_argument("--base", help="git ref to compare against for breaking changes")
    li.set_defaults(fn=cmd_lint)
    n = sub.add_parser("new", help="scaffold a new data product")
    n.add_argument("name")
    n.add_argument("--domain", required=True)
    n.add_argument("--team", required=True)
    n.add_argument("--upstream", nargs="*")
    n.set_defaults(fn=cmd_new)
    q = sub.add_parser("query", help="run SQL through the gateway")
    q.add_argument("sql")
    q.add_argument("--user", default="ana")
    q.set_defaults(fn=cmd_query)
    k = sub.add_parser("ask", help="ask a question in plain language")
    k.add_argument("question")
    k.add_argument("--user", default="pia")
    k.add_argument("--parser", choices=["sim", "claude"], default=None)
    k.add_argument("-v", "--verbose", action="store_true")
    k.set_defaults(fn=cmd_ask)
    s = sub.add_parser("serve", help="the API and console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    sm = sub.add_parser("simulate", help="the 30-day workload simulation against the baseline")
    sm.add_argument("--days", type=int, default=None)
    sm.set_defaults(fn=cmd_simulate)
    d = sub.add_parser("drill", help="run the failure drills and write their timelines")
    d.add_argument("--only", nargs="*")
    d.set_defaults(fn=cmd_drill)
    sub.add_parser("budget", help="error budget policy: exits 3 when deploys are frozen").set_defaults(fn=cmd_budget)
    sh = sub.add_parser("shadow", help="replay the audit log under a candidate access policy")
    sh.add_argument("roles", help="candidate roles.yaml")
    sh.add_argument("--limit", type=int, default=None)
    sh.set_defaults(fn=cmd_shadow)
    h = sub.add_parser("heldout", help="run a held-out set")
    h.add_argument("set")
    h.add_argument("--first-run", action="store_true")
    h.set_defaults(fn=cmd_heldout)
    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())


_ = dt
