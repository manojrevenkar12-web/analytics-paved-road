"""Build the marts with dbt and publish them to Iceberg with write-audit-publish."""

from __future__ import annotations

import datetime as dt
import os

import duckdb
import pyarrow as pa

from apr import conf
from apr.contracts.model import all_contracts
from apr.lake.catalog import warehouse_dir
from apr.lake.ingest import Run, write


def dbt_env() -> dict[str, str]:
    w = warehouse_dir()
    env = {"APR_WAREHOUSE": w, "DBT_PROFILES_DIR": str(conf.path("transform"))}
    kind = os.environ.get("APR_CATALOG", conf.read("platform")["lake"]["catalog"])
    if kind == "rest":
        env |= {
            "APR_DBT_CATALOG_TYPE": "rest",
            "APR_DBT_CATALOG_URI": os.environ.get("APR_ICEBERG_REST_URI", conf.read("platform")["lake"]["rest_uri"]),
        }
    else:
        env |= {
            "APR_DBT_CATALOG_TYPE": "sql",
            "APR_DBT_CATALOG_URI": f"sqlite:///{w}/catalog.db",
            "APR_DBT_CATALOG_WAREHOUSE": f"file://{w}/warehouse",
        }
    return env


def dbt(*args: str) -> bool:
    from dbt.cli.main import dbtRunner

    os.environ.update(dbt_env())
    res = dbtRunner().invoke(
        [*args, "--project-dir", str(conf.path("transform")), "--profiles-dir", str(conf.path("transform")), "--quiet"]
    )
    return bool(res.success)


def publish(day: dt.date) -> list[Run]:
    """Write each dbt-built mart to its Iceberg table through an audit branch."""
    out = []
    path = f"{warehouse_dir()}/transform.duckdb"
    with duckdb.connect(path) as con:
        for c in all_contracts().values():
            if c.build.kind != "dbt":
                continue
            tbl = con.sql(f"select * from main.{c.build.model}").to_arrow_table()
            schema = c.arrow_schema()
            tbl = pa.table({f.name: tbl.column(f.name).cast(f.type) for f in schema}, schema=schema)
            out.append(write(c, tbl, day))
    return out
