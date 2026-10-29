"""The answer is written by code from the result rows, so every number in it is a number the
gateway returned. A model may parse the question; it never writes a figure."""

from __future__ import annotations

import datetime as dt
from typing import Any

from apr.semantic.layer import MetricResult, catalog

COUNTRY_NAMES = {
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "AT": "Austria",
    "NL": "the Netherlands",
    "SE": "Sweden",
    "BE": "Belgium",
    "PT": "Portugal",
    "IE": "Ireland",
    "FI": "Finland",
    "GR": "Greece",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "LT": "Lithuania",
    "LV": "Latvia",
    "EE": "Estonia",
    "LU": "Luxembourg",
}


def fmt(v: Any, unit: str) -> str:
    if v is None:
        return "no data"
    x = float(v)
    if unit == "eur":
        a = abs(x)
        s = (
            f"€{a / 1e9:.2f}bn"
            if a >= 1e9
            else f"€{a / 1e6:.2f}M"
            if a >= 1e6
            else f"€{a / 1e3:.1f}k"
            if a >= 1e4
            else f"€{a:,.2f}"
        )
        return ("−" if x < 0 else "") + s
    return f"{x:,.0f}" if x == int(x) else f"{x:,.1f}"


def period_text(start: dt.date, end: dt.date) -> str:
    if start == end:
        return f"on {start.day} {start:%B %Y}"
    if start.year == end.year:
        if start.month == end.month:
            return f"from {start.day} to {end.day} {end:%B %Y}"
        return f"from {start.day} {start:%B} to {end.day} {end:%B %Y}"
    return f"from {start.day} {start:%B %Y} to {end.day} {end:%B %Y}"


ASSET_NAMES = {"stock": "stocks", "etf": "ETFs", "crypto": "crypto", "bond": "bonds"}


def value_text(dim: str, v: Any) -> str:
    if dim == "country":
        return COUNTRY_NAMES.get(str(v), str(v))
    if dim == "asset_class":
        return ASSET_NAMES.get(str(v), str(v))
    if dim == "period":
        return str(v)
    return str(v).replace("_", " ")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def write(res: MetricResult) -> str:
    if res.refused:
        return res.refused
    cat = catalog()
    q = res.query
    when = period_text(q.start, q.end)
    filt = ""
    if q.filters:
        parts = [
            f"{', '.join(value_text(f.dimension, v) for v in f.values)}"
            for f in q.filters
            if f.dimension not in q.dimensions
        ]
        filt = f" ({'; '.join(parts)})" if parts else ""
    if not res.rows:
        return f"There is no data {when}{filt}."
    keys = res.columns[: len(res.columns) - len(q.metrics)]
    sentences = []
    if all(v is None for r in res.rows for v in r[len(keys) :]):
        return f"There is no data {when}{filt}."
    if not keys:
        row = res.rows[0]
        for i, m in enumerate(q.metrics):
            spec = cat["metrics"][m]
            sentences.append(f"{spec['label']} {when}{filt}: {fmt(row[len(keys) + i], spec['unit'])}.")
        return " ".join(sentences)
    m0 = q.metrics[0]
    spec = cat["metrics"][m0]
    idx = len(keys)
    label = lambda row: ", ".join(value_text(k, row[j]) for j, k in enumerate(keys))  # noqa: E731
    if q.limit == 1 and q.order != "natural":
        row = res.rows[0]
        if spec["unit"] == "count":
            word = "most" if q.order == "metric_desc" else "fewest"
        else:
            word = "highest" if q.order == "metric_desc" else "lowest"
        return _cap(f"{label(row)} had the {word} {spec['label'].lower()} {when}{filt}: {fmt(row[idx], spec['unit'])}.")
    ordered = (
        res.rows
        if keys == ["period"] or q.order != "natural"
        else sorted(res.rows, key=lambda r: -(float(r[idx]) if r[idx] is not None else float("-inf")))
    )
    shown = ordered[:5]
    listing = "; ".join(f"{label(r)} {fmt(r[idx], spec['unit'])}" for r in shown)
    more = f" ({len(res.rows) - len(shown)} more in the table)" if len(res.rows) > len(shown) else ""
    if keys == ["period"]:
        total = (
            sum(float(r[idx] or 0) for r in res.rows)
            if spec["unit"] in ("eur", "count") and "/" not in spec["expr"] and "DISTINCT" not in spec["expr"]
            else None
        )
        lead = f"{spec['label']} {when}{filt}, by {q.grain}: {listing}{more}."
        return lead + (f" Total {fmt(total, spec['unit'])}." if total is not None else "")
    return f"{spec['label']} {when}{filt}: {listing}{more}."
