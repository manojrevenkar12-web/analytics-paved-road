"""HTTP API of the gateway and the console. Identity comes from the X-User header, which the
ingress sets from the OIDC session (oauth2-proxy in deploy/k8s); the API never trusts a role
sent by the client, only the user, and looks the role up in config/roles.yaml."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from apr import conf
from apr.ask.service import ask, data_through
from apr.contracts.model import all_contracts
from apr.gateway import audit, metrics
from apr.gateway.admission import Admission
from apr.gateway.core import Gateway, Result
from apr.gateway.planner import Refused
from apr.semantic import layer
from apr.semantic.layer import MetricQuery


class QueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    dry_run: bool = False


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="analytics-paved-road", version="0.1.0")
    gw = gateway or Gateway()
    adm = Admission()
    app.state.gateway, app.state.admission = gw, adm

    def user_of(x_user: str | None) -> str:
        if not x_user:
            raise HTTPException(401, "Sign in first (X-User is set by the ingress).")
        if x_user not in conf.read("roles")["users"]:
            raise HTTPException(403, f"{x_user!r} has no access to the analytics plane.")
        return x_user

    async def run_query(sql: str, user: str) -> Result:
        try:
            plan = gw.plan(sql, user)
        except Refused:
            return gw.query(sql, user)  # records the refusal with its rule
        try:
            async with adm.slot(plan.lane, user):
                return await anyio.to_thread.run_sync(lambda: gw.query(sql, user, plan))
        except Refused as e:
            r = Result("-", "refused", user, plan.role, e.rule, e.message, e.status, lane=plan.lane, sql=sql)
            metrics.REFUSALS.labels(e.rule, plan.role).inc()
            # a full queue is the platform's failure (it counts against availability); a user's own limit is not
            metrics.REQUESTS.labels(plan.lane, plan.role, "queue_full" if e.rule == "queue_full" else "refused").inc()
            audit.append(
                {
                    "user": user,
                    "role": plan.role,
                    "status": "refused",
                    "rule": e.rule,
                    "sql": sql,
                    "lane": plan.lane,
                    "est_bytes": plan.est_bytes,
                }
            )
            return r

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "spark_lane": gw.spark.available}

    @app.get("/metrics")
    def prom() -> Response:
        for c in all_contracts().values():
            try:
                snap = gw.planner.catalog.load_table(c.table).current_snapshot()
            except Exception:
                continue
            if snap and snap.summary and snap.summary.get("apr.day"):
                day = dt.date.fromisoformat(snap.summary["apr.day"])
                end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.UTC)
                metrics.FRESH_DAY.labels(c.name).set(end.timestamp())
                landed = dt.datetime.fromisoformat(snap.summary["apr.landed_at"]).replace(tzinfo=dt.UTC)
                metrics.FRESHNESS.labels(c.name).set(landed.timestamp())
                h, m = map(int, c.freshness.expected_by.split(":"))
                due = end + dt.timedelta(hours=h, minutes=m)
                metrics.FRESH_ON_TIME.labels(c.name).set(1.0 if landed <= due else 0.0)
        return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/me")
    def me(x_user: str | None = Header(default=None)) -> dict[str, Any]:
        user = user_of(x_user)
        roles = conf.read("roles")
        role = roles["users"][user]
        return {
            "user": user,
            "role": role,
            **{k: roles["roles"][role].get(k, []) for k in ("clear", "masked", "denied")},
            "max_scan_bytes": roles["roles"][role]["max_scan_bytes"],
        }

    @app.get("/api/users")
    def users() -> list[dict[str, str]]:
        roles = conf.read("roles")
        return [
            {"user": u, "role": r, "description": roles["roles"][r]["description"]} for u, r in roles["users"].items()
        ]

    @app.post("/api/query")
    async def query(body: QueryIn, x_user: str | None = Header(default=None)) -> JSONResponse:
        user = user_of(x_user)
        if body.dry_run:
            try:
                p = gw.plan(body.sql, user)
            except Refused as e:
                return JSONResponse({"status": "refused", "rule": e.rule, "message": e.message}, status_code=200)
            return JSONResponse(
                {
                    "status": "ok",
                    "lane": p.lane,
                    "est_bytes": p.est_bytes,
                    "role": p.role,
                    "masked": p.masked,
                    "row_limit": p.row_limit,
                    "limit_added": p.limit_added,
                    "scans": [
                        {
                            "table": s.table,
                            "snapshot_id": s.snapshot_id,
                            "columns": sorted(s.columns),
                            "files": s.files,
                            "files_total": s.files_total,
                            "est_bytes": s.est_bytes,
                            "est_rows": s.est_rows,
                            "filter": str(s.row_filter),
                        }
                        for s in p.scans
                    ],
                    "physical": p.physical[p.lane],
                }
            )
        r = await run_query(body.sql, user)
        return JSONResponse(r.to_dict(), status_code=r.http_status)

    @app.get("/api/catalog")
    def catalog_(x_user: str | None = Header(default=None)) -> list[dict[str, Any]]:
        user = user_of(x_user)
        role = conf.read("roles")["users"][user]
        out = []
        for c in all_contracts().values():
            snap = None
            try:
                t = gw.planner.catalog.load_table(c.table)
                snap = t.current_snapshot()
                snapshots = len(t.snapshots())
            except Exception:
                snapshots = 0
            s = (snap.summary.additional_properties if snap and snap.summary else {}) or {}
            out.append(
                {
                    "name": c.name,
                    "table": c.table,
                    "layer": c.layer,
                    "domain": c.domain,
                    "description": c.description,
                    "owner": c.owner.model_dump(),
                    "freshness": c.freshness.model_dump(),
                    "partitioning": [p.model_dump() for p in c.partitioning],
                    "require_partition_filter": c.require_partition_filter,
                    "upstream": c.upstream,
                    "consumers": c.consumers,
                    "columns": [
                        {
                            "name": col.name,
                            "type": col.type,
                            "class": col.classification,
                            "access": gw.planner.policy.treatment(role, col.classification),
                        }
                        for col in c.columns
                    ],
                    "snapshot_id": snap.snapshot_id if snap else None,
                    "snapshots": snapshots,
                    "data_through": s.get("apr.day"),
                    "landed_at": s.get("apr.landed_at"),
                    "rows": int(s.get("total-records", 0)),
                    "files": int(s.get("total-data-files", 0)),
                    "bytes": int(s.get("total-files-size", 0)),
                }
            )
        return out

    @app.get("/api/semantic")
    def semantic() -> dict[str, Any]:
        cat = layer.catalog()
        try:
            through = data_through(gw.planner.catalog).isoformat()
        except Exception:
            through = None
        return {
            "metrics": [
                {
                    "name": k,
                    **{x: v[x] for x in ("label", "description", "unit", "source")},
                    "dimensions": list(cat["sources"][v["source"]]["dimensions"]),
                    "table": cat["sources"][v["source"]]["table"],
                }
                for k, v in cat["metrics"].items()
            ],
            "dimensions": [
                {"name": k, "label": v["label"], "values": v.get("values", [])} for k, v in cat["dimensions"].items()
            ],
            "data_through": through,
        }

    @app.post("/api/semantic/query")
    async def semantic_query(q: MetricQuery, x_user: str | None = Header(default=None)) -> dict[str, Any]:
        user = user_of(x_user)
        through = data_through(gw.planner.catalog)
        res = await anyio.to_thread.run_sync(lambda: layer.run(q, user, gw, through))
        from apr.ask.answer import write

        return {
            "text": write(res),
            "answered": res.refused is None,
            "rule": res.rule,
            "sql": [c.sql for c in res.compiled],
            "columns": res.columns,
            "rows": res.rows,
        }

    @app.post("/api/ask")
    async def ask_(body: AskIn, x_user: str | None = Header(default=None)) -> dict[str, Any]:
        user = user_of(x_user)
        a = await anyio.to_thread.run_sync(lambda: ask(body.question, user, gw))
        return a.__dict__

    @app.get("/api/audit")
    def audit_(limit: int = 50, x_user: str | None = Header(default=None)) -> list[dict[str, Any]]:
        user = user_of(x_user)
        role = conf.read("roles")["users"][user]
        rows = audit.read()
        if role != "compliance":
            rows = [r for r in rows if r.get("user") == user]
        return rows[-min(limit, 500) :][::-1]

    @app.get("/api/slo")
    def slo() -> JSONResponse:
        p = conf.path("reports", "slo", "status.json")
        if not p.exists():
            return JSONResponse({"error": "No SLO report yet; run `apr simulate`."}, status_code=404)
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/api/drills")
    def drills() -> JSONResponse:
        p = conf.path("reports", "drills", "drills.json")
        if not p.exists():
            return JSONResponse({"error": "No drill report yet; run `apr drill`."}, status_code=404)
        return JSONResponse(json.loads(p.read_text()))

    static = Path(os.environ.get("APR_STATIC_ROOT", conf.path("console", "dist")))
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request) -> FileResponse:
            f = static / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app
