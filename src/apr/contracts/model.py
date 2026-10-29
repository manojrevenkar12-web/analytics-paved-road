"""Data product contracts: the one place a product's schema, classification, partitioning,
owner and freshness objective are written down. Everything else is generated from them:
the Iceberg table, dbt tests, the Airflow tasks, the Grafana panel and the alert rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import pyarrow as pa
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apr import conf

ColumnType = Literal["string", "long", "double", "date", "timestamp", "boolean"]
Classification = Literal["public", "internal", "financial", "pii_quasi", "pii_direct"]
Transform = Literal["identity", "day", "month", "year"]


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: ColumnType
    # named classification in the YAML; "class" is a Python keyword
    classification: Classification = Field(alias="class")
    nullable: bool = True
    unique: bool = False
    accepted: list[str] | None = None


class Partition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    transform: Transform


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str
    contact: str


class Freshness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: Literal["daily", "hourly"]
    expected_by: str = Field(pattern=r"^\d{2}:\d{2}$")
    objective: float = Field(gt=0.5, lt=1.0)


class Build(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ingest", "dbt"]
    model: str | None = None


class QualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: Literal["row_count_min", "positive", "not_null", "unique", "accepted_values"]
    column: str | None = None
    value: float | None = None


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    table: str = Field(pattern=r"^(raw|mart)\.[a-z][a-z0-9_]*$")
    layer: Literal["raw", "mart"]
    domain: str
    owner: Owner
    description: str
    build: Build
    freshness: Freshness
    columns: list[Column]
    partitioning: list[Partition] = []
    require_partition_filter: bool = False
    quality: list[QualityCheck] = []
    upstream: list[str] = []
    consumers: list[str] = []

    @field_validator("columns")
    @classmethod
    def unique_names(cls, v: list[Column]) -> list[Column]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            raise ValueError("duplicate column names")
        return v

    @model_validator(mode="after")
    def consistent(self) -> Contract:
        names = {c.name for c in self.columns}
        for p in self.partitioning:
            if p.source not in names:
                raise ValueError(f"partition source {p.source!r} is not a column")
        for q in self.quality:
            if q.column and q.column not in names:
                raise ValueError(f"quality check on unknown column {q.column!r}")
        if self.layer != self.table.split(".")[0]:
            raise ValueError("layer must match the table's namespace")
        if self.build.kind == "dbt" and not self.build.model:
            raise ValueError("dbt products need build.model")
        if self.require_partition_filter and not self.partitioning:
            raise ValueError("require_partition_filter needs a partition spec")
        return self

    @property
    def namespace(self) -> str:
        return self.table.split(".")[0]

    @property
    def short(self) -> str:
        return self.table.split(".")[1]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    def arrow_schema(self) -> pa.Schema:
        return pa.schema([pa.field(c.name, ARROW[c.type], nullable=c.nullable) for c in self.columns])


ARROW: dict[str, pa.DataType] = {
    "string": pa.string(),
    "long": pa.int64(),
    "double": pa.float64(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
    "boolean": pa.bool_(),
}


def load(path: str | Path) -> Contract:
    with open(path, encoding="utf-8") as f:
        return Contract.model_validate(yaml.safe_load(f))


@lru_cache(maxsize=1)
def all_contracts() -> dict[str, Contract]:
    out: dict[str, Contract] = {}
    for p in sorted(conf.path("config", "products").glob("*.yaml")):
        c = load(p)
        out[c.table] = c
    return out


def by_name(name: str) -> Contract:
    for c in all_contracts().values():
        if c.name == name:
            return c
    raise KeyError(name)
