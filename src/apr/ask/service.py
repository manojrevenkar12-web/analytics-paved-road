"""Question in, grounded answer out."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any

from apr import conf
from apr.ask import answer
from apr.semantic import layer
from apr.semantic.layer import MetricQuery, Unanswerable


@dataclass
class Answer:
    question: str
    text: str
    answered: bool
    parser: str
    data_through: str
    query: dict[str, Any] | None = None
    sql: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    rule: str | None = None


def data_through(catalog=None) -> dt.date:
    """The last complete business day in the marts (from Iceberg snapshot properties)."""
    from apr.lake.catalog import catalog as default

    cat = catalog or default()
    days = []
    for name, m in layer.catalog()["sources"].items():
        snap = cat.load_table(m["table"]).current_snapshot()
        if snap and snap.summary and snap.summary.get("apr.day"):
            days.append(dt.date.fromisoformat(str(snap.summary["apr.day"])))
        _ = name
    if not days:
        raise RuntimeError("no published data")
    return min(days)


def parse(question: str, ref: dt.date, parser: str | None = None) -> MetricQuery:
    which = parser or os.environ.get("APR_ASK") or conf.read("ask")["parser"]
    if which == "claude":
        from apr.ask import claude

        return claude.parse(question, ref)
    from apr.ask import parse_sim

    return parse_sim.parse(question, ref)


def ask(question: str, user: str, gateway, parser: str | None = None, ref: dt.date | None = None) -> Answer:
    which = parser or os.environ.get("APR_ASK") or conf.read("ask")["parser"]
    ref = ref or data_through(gateway.planner.catalog)
    try:
        q = parse(question, ref, which)
    except Unanswerable as e:
        return Answer(question, e.reason, False, which, ref.isoformat(), rule="not_in_semantic_layer")
    res = layer.run(q, user, gateway, ref)
    return Answer(
        question,
        answer.write(res),
        res.refused is None,
        which,
        ref.isoformat(),
        q.model_dump(mode="json"),
        [c.sql for c in res.compiled],
        res.columns,
        res.rows,
        res.rule,
    )
