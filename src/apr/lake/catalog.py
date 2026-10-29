"""The Iceberg catalog: a local SQLite catalog for development, the REST catalog (backed by
S3/MinIO, or AWS Glue's Iceberg REST endpoint in production) everywhere else. Tables, their
partition specs and properties are created from the data product contracts."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from functools import lru_cache

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.table import Table
from pyiceberg.transforms import DayTransform, IdentityTransform, MonthTransform, YearTransform

from apr import conf
from apr.contracts.model import Contract, all_contracts

TRANSFORMS = {"identity": IdentityTransform, "day": DayTransform, "month": MonthTransform, "year": YearTransform}


def warehouse_dir() -> str:
    d = os.environ.get("APR_WAREHOUSE") or str(conf.path(conf.read("platform")["lake"]["local_warehouse"]))
    os.makedirs(d, exist_ok=True)
    return d


@lru_cache(maxsize=4)
def _catalog(kind: str, where: str) -> Catalog:
    lake = conf.read("platform")["lake"]
    if kind == "rest":
        props = {"type": "rest", "uri": os.environ.get("APR_ICEBERG_REST_URI", lake["rest_uri"])}
        endpoint = os.environ.get("APR_S3_ENDPOINT", lake["s3_endpoint"])
        if endpoint:
            props.update(
                {
                    "s3.endpoint": endpoint,
                    "s3.path-style-access": "true",
                    "s3.access-key-id": os.environ.get("AWS_ACCESS_KEY_ID", ""),
                    "s3.secret-access-key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                    "s3.region": os.environ.get("AWS_REGION", "eu-central-1"),
                }
            )
        return load_catalog("lake", **props)
    return SqlCatalog("lake", uri=f"sqlite:///{where}/catalog.db", warehouse=f"file://{where}/warehouse")


def catalog() -> Catalog:
    kind = os.environ.get("APR_CATALOG", conf.read("platform")["lake"]["catalog"])
    return _catalog(kind, warehouse_dir())


def reset_cache() -> None:
    _catalog.cache_clear()


def contract_hash(c: Contract) -> str:
    return hashlib.sha256(json.dumps(c.model_dump(by_alias=True), sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]


def ensure_table(c: Contract, cat: Catalog | None = None) -> Table:
    cat = cat or catalog()
    with contextlib.suppress(NamespaceAlreadyExistsError):
        cat.create_namespace(c.namespace)
    try:
        t = cat.load_table(c.table)
    except NoSuchTableError:
        t = cat.create_table(
            c.table,
            schema=c.arrow_schema(),
            properties={
                "apr.owner": c.owner.team,
                "apr.contract": contract_hash(c),
                "apr.domain": c.domain,
                "write.parquet.compression-codec": "zstd",
                "format-version": "2",
            },
        )
        if c.partitioning:
            with t.update_spec() as spec:
                for p in c.partitioning:
                    spec.add_field(p.source, TRANSFORMS[p.transform](), f"{p.source}_{p.transform}")
        t = cat.load_table(c.table)
    return t


def ensure_all() -> dict[str, Table]:
    return {name: ensure_table(c) for name, c in all_contracts().items()}


def load(table: str) -> Table:
    return catalog().load_table(table)
