"""Metrics defined once (config/metrics.yaml) and compiled to Spark SQL. The compiled SQL goes
through the gateway as the asking user, so the semantic layer has no access of its own."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from apr import conf

Grain = Literal["day", "week", "month", "quarter", "year"]
GRAIN_ORDER = ["day", "week", "month", "quarter", "year"]


class Filter(BaseModel):
    dimension: str
    values: list[str] = Field(min_length=1)


class MetricQuery(BaseModel):
    metrics: list[str] = Field(min_length=1, max_length=4)
    dimensions: list[str] = []
    grain: Grain | None = None
    start: dt.date
    end: dt.date  # inclusive
    filters: list[Filter] = []
    order: Literal["metric_desc", "metric_asc", "natural"] = "natural"
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def window(self) -> MetricQuery:
        if self.end < self.start:
            raise ValueError("end is before start")
        return self


class Unanswerable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    return conf.read("metrics")


@dataclass
class Compiled:
    source: str
    metrics: list[str]
    sql: str
    keys: list[str]  # period and dimension column names


@dataclass
class MetricResult:
    query: MetricQuery
    compiled: list[Compiled]
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    refused: str | None = None
    rule: str | None = None


def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def validate(q: MetricQuery, data_through: dt.date) -> None:
    cat = catalog()
    for m in q.metrics:
        if m not in cat["metrics"]:
            raise Unanswerable(f"{m!r} is not a defined metric.")
    for d in q.dimensions + [f.dimension for f in q.filters]:
        if d not in cat["dimensions"]:
            raise Unanswerable(f"{d!r} is not a defined dimension.")
    for f in q.filters:
        allowed = cat["dimensions"][f.dimension].get("values")
        if allowed and any(v not in allowed for v in f.values):
            raise Unanswerable(f"{f.dimension} has the values {', '.join(allowed)}.")
    for m in q.metrics:
        src = cat["sources"][cat["metrics"][m]["source"]]
        label = cat["metrics"][m]["label"]
        for d in q.dimensions + [f.dimension for f in q.filters]:
            if d not in src["dimensions"]:
                raise Unanswerable(
                    f"{label} cannot be broken down by {cat['dimensions'][d]['label'].lower()}; "
                    f"it has {', '.join(cat['dimensions'][x]['label'].lower() for x in src['dimensions'])}."
                )
        floor = src.get("grain_floor")
        if floor:
            if q.grain and GRAIN_ORDER.index(q.grain) < GRAIN_ORDER.index(floor):
                raise Unanswerable(f"{label} is only available by {floor}.")
            end_ok = q.end == _month_end(q.end) or q.end >= data_through
            if q.start != _month_start(q.start) or not end_ok:
                raise Unanswerable(
                    f"{label} is only available for whole months; ask for a month, a quarter or a range of months."
                )


def compile_query(q: MetricQuery) -> list[Compiled]:
    cat = catalog()
    by_source: dict[str, list[str]] = {}
    for m in q.metrics:
        by_source.setdefault(cat["metrics"][m]["source"], []).append(m)
    out = []
    for name, metrics in by_source.items():
        src = cat["sources"][name]
        a = src["alias"]
        time_col = f"{a}.{src['time']}"
        select: list[str] = []
        keys: list[str] = []
        if q.grain:
            select.append(f"CAST(DATE_TRUNC('{q.grain.upper()}', {time_col}) AS DATE) AS period")
            keys.append("period")
        for d in q.dimensions:
            select.append(f"{src['dimensions'][d]} AS {d}")
            keys.append(d)
        for m in metrics:
            select.append(f"{cat['metrics'][m]['expr']} AS {m}")
        used_aliases = {src["dimensions"][d].split(".")[0] for d in q.dimensions + [f.dimension for f in q.filters]}
        joins = "".join(
            f" JOIN {j['table']} {ja} ON {j['condition']}"
            for ja, j in (src.get("joins") or {}).items()
            if ja in used_aliases
        )
        end_excl = q.end + dt.timedelta(days=1)
        where = [f"{time_col} >= DATE '{q.start.isoformat()}'", f"{time_col} < DATE '{end_excl.isoformat()}'"]
        for f in q.filters:
            vals = ", ".join("'" + v.replace("'", "''") + "'" for v in f.values)
            where.append(f"{src['dimensions'][f.dimension]} IN ({vals})")
        sql = f"SELECT {', '.join(select)} FROM {src['table']} {a}{joins} WHERE {' AND '.join(where)}"
        if keys:
            sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(keys)))
        if q.order != "natural" and len(by_source) == 1:
            sql += f" ORDER BY {metrics[0]} {'DESC' if q.order == 'metric_desc' else 'ASC'}"
            if q.limit:
                sql += f" LIMIT {q.limit}"
        elif keys:
            sql += " ORDER BY " + ", ".join(str(i + 1) for i in range(len(keys)))
        out.append(Compiled(name, metrics, sql, keys))
    return out


def run(q: MetricQuery, user: str, gateway, data_through: dt.date) -> MetricResult:
    try:
        validate(q, data_through)
    except Unanswerable as e:
        return MetricResult(q, [], refused=e.reason, rule="not_in_semantic_layer")
    compiled = compile_query(q)
    res = MetricResult(q, compiled)
    merged: dict[tuple, dict[str, Any]] = {}
    keys = compiled[0].keys
    for c in compiled:
        r = gateway.query(c.sql, user)
        if r.status != "ok":
            res.refused, res.rule = r.message, r.rule
            return res
        idx = {name: i for i, name in enumerate(r.columns)}
        for row in r.rows:
            k = tuple(row[idx[x]] for x in keys)
            merged.setdefault(k, {}).update({m: row[idx[m]] for m in c.metrics})
    items = list(merged.items())
    if len(compiled) > 1:
        items.sort(key=lambda kv: tuple("" if v is None else str(v) for v in kv[0]))
        if q.order != "natural":
            m0 = q.metrics[0]
            items.sort(key=lambda kv: kv[1].get(m0) or 0, reverse=q.order == "metric_desc")
            if q.limit:
                items = items[: q.limit]
    res.columns = keys + q.metrics
    res.rows = [list(k) + [vals.get(m) for m in q.metrics] for k, vals in items]
    return res
