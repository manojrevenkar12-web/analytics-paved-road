"""Daily ingestion with write-audit-publish on Iceberg branches.

Each batch is written to an audit branch, checked against its contract there, and only then
published by moving `main` to the audited snapshot. Readers of `main` never see a batch that
failed its checks; the failed snapshot is left unreferenced and the run is quarantined.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field

import pyarrow as pa
import pyarrow.compute as pc

from apr import conf
from apr.contracts.model import Contract, all_contracts
from apr.lake.catalog import ensure_table, warehouse_dir

DIMENSION_TABLES = {"raw.customers", "raw.instruments", "raw.savings_plans"}  # replaced each load


@dataclass
class CheckResult:
    check: str
    column: str | None
    ok: bool
    detail: str = ""


@dataclass
class Run:
    table: str
    day: str
    status: str  # published | quarantined
    rows: int
    snapshot_id: int | None
    landed_at: str
    checks: list[CheckResult] = field(default_factory=list)


def audit(c: Contract, batch: pa.Table, total_records: int) -> list[CheckResult]:
    """The contract's checks, on the batch (cheap) and on table metadata (no scan)."""
    out: list[CheckResult] = []
    for col in c.columns:
        arr = batch.column(col.name)
        if not col.nullable:
            nulls = arr.null_count
            out.append(CheckResult("not_null", col.name, nulls == 0, f"{nulls} nulls" if nulls else ""))
        if col.accepted:
            bad = pc.sum(pc.invert(pc.is_in(arr.drop_null(), value_set=pa.array(col.accepted)))).as_py() or 0
            out.append(
                CheckResult(
                    "accepted_values", col.name, bad == 0, f"{bad} values outside {col.accepted}" if bad else ""
                )
            )
        if col.unique:
            dup = len(arr) - len(pc.unique(arr))
            out.append(CheckResult("unique", col.name, dup == 0, f"{dup} duplicates" if dup else ""))
    for q in c.quality:
        if q.check == "positive" and q.column:
            bad = pc.sum(pc.less_equal(batch.column(q.column), 0)).as_py() or 0
            out.append(CheckResult("positive", q.column, bad == 0, f"{bad} rows <= 0" if bad else ""))
        elif q.check == "row_count_min" and q.value is not None:
            out.append(
                CheckResult(
                    "row_count_min",
                    None,
                    total_records >= q.value,
                    f"{total_records} rows in the table" if total_records < q.value else "",
                )
            )
    return out


def landed_at(day: dt.date, c: Contract, delay_min: float = 0.0) -> dt.datetime:
    """The simulated time the batch for `day` lands: 04:30 the next morning plus any delay."""
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(4, 30)) + dt.timedelta(minutes=delay_min)


def conform(c: Contract, batch: pa.Table) -> tuple[pa.Table | None, list[CheckResult]]:
    """The batch in the contract's schema, or the reasons it cannot be (nothing is written then)."""
    problems: list[CheckResult] = []
    cols = []
    schema = c.arrow_schema()
    for f in schema:
        if f.name not in batch.column_names:
            problems.append(CheckResult("schema", f.name, False, "column missing"))
            continue
        arr = batch.column(f.name)
        try:
            arr = arr.cast(f.type)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
            problems.append(CheckResult("schema", f.name, False, f"cannot be read as {f.type}: {e}"))
            continue
        if not f.nullable and arr.null_count:
            problems.append(CheckResult("not_null", f.name, False, f"{arr.null_count} nulls"))
        cols.append(arr)
    extra = set(batch.column_names) - {f.name for f in schema}
    if extra:
        problems.append(CheckResult("schema", None, False, f"columns not in the contract: {sorted(extra)}"))
    if problems:
        return None, problems
    return pa.Table.from_arrays(cols, schema=schema), []


def write(c: Contract, batch: pa.Table, day: dt.date, delay_min: float = 0.0) -> Run:
    t = ensure_table(c)
    conformed, problems = conform(c, batch)
    if conformed is None:
        run = Run(
            c.table,
            day.isoformat(),
            "quarantined",
            batch.num_rows,
            None,
            landed_at(day, c, delay_min).isoformat(),
            problems,
        )
        log_run(run)
        return run
    batch = conformed
    props = {
        "apr.day": day.isoformat(),
        "apr.landed_at": landed_at(day, c, delay_min).isoformat(),
        "apr.rows": str(batch.num_rows),
    }
    branch = f"audit-{day.isoformat()}"
    head = t.current_snapshot()
    if head is not None:
        t.manage_snapshots().create_branch(head.snapshot_id, branch).commit()
        t = t.refresh()
    if c.table in DIMENSION_TABLES:
        t.overwrite(batch, snapshot_properties=props, branch=branch if head else "main")
    else:
        t.append(batch, snapshot_properties=props, branch=branch if head else "main")
    t = t.refresh()
    snap = t.snapshot_by_name(branch) if head else t.current_snapshot()
    assert snap is not None
    total = int(snap.summary.get("total-records", "0")) if snap.summary else 0
    checks = audit(c, batch, total)
    ok = all(x.ok for x in checks)
    if head is not None:
        ms = t.manage_snapshots()
        if ok:
            ms = ms.set_current_snapshot(snapshot_id=snap.snapshot_id)
        ms.remove_branch(branch).commit()
    elif not ok:
        # first load failed: roll the table back to empty is not possible; drop the data
        t.overwrite(batch.slice(0, 0))
    run = Run(
        c.table,
        day.isoformat(),
        "published" if ok else "quarantined",
        batch.num_rows,
        snap.snapshot_id if ok else None,
        props["apr.landed_at"],
        checks,
    )
    log_run(run)
    return run


def log_run(run: Run) -> None:
    with open(f"{warehouse_dir()}/runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(run), default=str) + "\n")


def runs() -> list[dict]:
    try:
        with open(f"{warehouse_dir()}/runs.jsonl", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def ingest_day(
    world, i: int, delay_min: dict[str, float] | None = None, tamper: dict[str, pa.Table] | None = None
) -> list[Run]:
    """Load day i of the world; `tamper` replaces a batch (drills), `delay_min` delays landing."""
    batches = world.day(i)
    contracts = all_contracts()
    out = []
    for table, batch in batches.items():
        if tamper and table in tamper:
            batch = tamper[table]
        out.append(write(contracts[table], batch, world.date(i), (delay_min or {}).get(table, 0.0)))
    return out


def ingest(days: int | None = None) -> list[Run]:
    from apr.world.generate import World

    w = World()
    n = days if days is not None else w.days
    done = {(r["table"], r["day"]) for r in runs() if r["status"] == "published"}
    out: list[Run] = []
    for i in range(n):
        if all((t, w.date(i).isoformat()) in done for t in all_contracts() if t.startswith("raw.")):
            continue
        out.extend(ingest_day(w, i))
    return out


def _cfg():  # pragma: no cover - keeps conf imported for callers that patch it
    return conf.read("platform")
