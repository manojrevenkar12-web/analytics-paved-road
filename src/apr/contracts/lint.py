"""`apr lint`: the paved road's policy checks, run in CI on every change to a contract.

Errors block the merge; warnings are shown in the review. With --base, a contract is also
compared to its version on the base branch: removing or retyping a column, or lowering a
column's classification, is a breaking change for consumers and needs a new product version.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

import yaml

from apr import conf
from apr.contracts.model import Contract, all_contracts

PII_NAMES = re.compile(
    r"(^|_)(name|first_name|last_name|full_name|email|e_mail|iban|phone|mobile|address|street|"
    r"birth|birth_date|dob|tax_id|passport|ip_address)($|_)"
)
RANK = {"public": 0, "internal": 1, "financial": 2, "pii_quasi": 3, "pii_direct": 4}


@dataclass
class Finding:
    level: str  # error | warning
    product: str
    rule: str
    message: str


def lint_contract(c: Contract, contracts: dict[str, Contract]) -> list[Finding]:
    out: list[Finding] = []
    by_name = {x.name: x for x in contracts.values()}
    if not c.owner.contact.startswith("#"):
        out.append(Finding("error", c.name, "owner_channel", "owner.contact must be a Slack channel (#team-data)"))
    for col in c.columns:
        if (
            PII_NAMES.search(col.name)
            and not col.classification.startswith("pii")
            and not (col.name == "name" and c.domain == "markets")
        ):
            out.append(
                Finding(
                    "error",
                    c.name,
                    "pii_classification",
                    f"{col.name} looks like personal data but is classified {col.classification}",
                )
            )
    has_event_time = any(col.type == "timestamp" and col.name.endswith(("_at", "_time")) for col in c.columns)
    if (
        c.layer == "raw"
        and has_event_time
        and c.build.kind == "ingest"
        and not c.partitioning
        and c.name not in ("customers", "savings_plans")
    ):
        out.append(
            Finding(
                "warning",
                c.name,
                "partitioning",
                "an event table without a partition spec is scanned in full by every query",
            )
        )
    if c.partitioning and any(p.transform == "day" for p in c.partitioning) and not c.require_partition_filter:
        out.append(
            Finding("warning", c.name, "partition_filter", "day-partitioned tables usually require a partition filter")
        )
    for u in c.upstream:
        if u not in by_name:
            out.append(Finding("error", c.name, "upstream", f"upstream {u!r} is not a data product"))
        elif _minutes(by_name[u].freshness.expected_by) >= _minutes(c.freshness.expected_by):
            out.append(
                Finding(
                    "error",
                    c.name,
                    "freshness_order",
                    f"due at {c.freshness.expected_by} but upstream {u} is only due at "
                    f"{by_name[u].freshness.expected_by}",
                )
            )
        elif by_name[u].freshness.objective < c.freshness.objective:
            out.append(
                Finding(
                    "warning",
                    c.name,
                    "freshness_objective",
                    f"objective {c.freshness.objective} is stricter than upstream {u} "
                    f"({by_name[u].freshness.objective})",
                )
            )
    for k in c.consumers:
        if k not in by_name:
            out.append(Finding("error", c.name, "consumer", f"consumer {k!r} is not a data product"))
        elif c.name not in by_name[k].upstream:
            out.append(Finding("error", c.name, "consumer", f"{k} does not list {c.name} as upstream"))
    if c.build.kind == "dbt":
        model = conf.path("transform", "models", "marts", f"{c.build.model}.sql")
        if not model.exists():
            out.append(Finding("error", c.name, "dbt_model", f"{model.relative_to(conf.ROOT)} does not exist"))
    if not c.description.strip() or len(c.description) < 12:
        out.append(Finding("warning", c.name, "description", "describe what one row is"))
    return out


def breaking(old: Contract, new: Contract) -> list[Finding]:
    out = []
    for col in old.columns:
        n = new.column(col.name)
        if n is None:
            out.append(Finding("error", new.name, "breaking_removed", f"column {col.name} removed"))
            continue
        if n.type != col.type:
            out.append(Finding("error", new.name, "breaking_type", f"{col.name}: {col.type} -> {n.type}"))
        if RANK[n.classification] < RANK[col.classification]:
            out.append(
                Finding(
                    "error",
                    new.name,
                    "classification_lowered",
                    f"{col.name}: {col.classification} -> {n.classification} needs the data "
                    f"protection officer's sign-off (label dpo-approved)",
                )
            )
        if col.nullable is False and n.nullable:
            out.append(Finding("warning", new.name, "nullable", f"{col.name} may now be null"))
    if [p.model_dump() for p in old.partitioning] != [p.model_dump() for p in new.partitioning]:
        out.append(Finding("warning", new.name, "partition_evolution", "partition spec changes apply to new data only"))
    return out


def _minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _base_contracts(ref: str) -> dict[str, Contract]:
    files = subprocess.run(
        ["git", "ls-tree", "--name-only", ref, "config/products/"],
        cwd=conf.ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    out = {}
    for f in files:
        text = subprocess.run(
            ["git", "show", f"{ref}:{f}"], cwd=conf.ROOT, capture_output=True, text=True, check=False
        ).stdout
        if text:
            c = Contract.model_validate(yaml.safe_load(text))
            out[c.name] = c
    return out


def lint(base: str | None = None, contracts: dict[str, Contract] | None = None) -> list[Finding]:
    contracts = contracts or all_contracts()
    out: list[Finding] = []
    for c in contracts.values():
        out += lint_contract(c, contracts)
    if base:
        old = _base_contracts(base)
        new = {c.name: c for c in contracts.values()}
        for name, oc in old.items():
            if name not in new:
                out.append(Finding("error", name, "breaking_removed", "data product removed"))
            else:
                out += breaking(oc, new[name])
    return out
