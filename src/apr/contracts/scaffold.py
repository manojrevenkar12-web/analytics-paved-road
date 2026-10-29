"""`apr new`: a new data product from a template, consistent with the paved road from the start."""

from __future__ import annotations

from pathlib import Path

import yaml

from apr import conf
from apr.contracts.generate import write_dbt_schema
from apr.contracts.model import by_name


def scaffold(name: str, domain: str, team: str, upstream: list[str]) -> list[Path]:
    for u in upstream:
        by_name(u)  # raises KeyError for an unknown upstream
    contract = {
        "name": name,
        "table": f"mart.{name}",
        "layer": "mart",
        "domain": domain,
        "owner": {"team": team, "contact": f"#{team}"},
        "description": "TODO: what one row is",
        "build": {"kind": "dbt", "model": name},
        "freshness": {"schedule": "daily", "expected_by": "08:00", "objective": 0.99},
        "upstream": upstream,
        "columns": [{"name": "day", "type": "date", "class": "public", "nullable": False}],
        "partitioning": [{"source": "day", "transform": "month"}],
        "quality": [{"check": "row_count_min", "value": 1}],
    }
    cp = conf.path("config", "products", f"{name}.yaml")
    if cp.exists():
        raise FileExistsError(cp)
    cp.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    refs = "\n".join(
        f"-- upstream: {{{{ ref('{by_name(u).build.model}') }}}}"
        if by_name(u).build.kind == "dbt"
        else f"-- upstream: {{{{ source('raw', '{by_name(u).short}') }}}}"
        for u in upstream
    )
    mp = conf.path("transform", "models", "marts", f"{name}.sql")
    mp.write_text(
        f"-- {name}: owned by {team}. Columns must match config/products/{name}.yaml.\n{refs}\n"
        "select cast(current_date as date) as day\n",
        encoding="utf-8",
    )
    from apr.contracts import model

    model.all_contracts.cache_clear()
    return [cp, mp, write_dbt_schema()]
