"""Plan a query before anything runs: parse, check it against the access policy, estimate what
it will scan from Iceberg metadata, choose a lane, and rewrite it into the physical query.

Users write Spark SQL (the house dialect). The planner refuses with a rule name and a message
that says how to fix the query; it never silently changes what a query means, except to cap the
rows an interactive query returns (and it says so).

Access is enforced by rewriting, not by trusting the engine: every table reference becomes a
sub-query over exactly the columns the role may see, with masked columns already masked, so a
masked or denied value never leaves storage in clear whichever lane runs the query.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb
import sqlglot
from pyiceberg.expressions import (
    AlwaysTrue,
    And,
    BooleanExpression,
    EqualTo,
    GreaterThan,
    GreaterThanOrEqual,
    In,
    LessThan,
    LessThanOrEqual,
    Or,
)
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from apr import conf
from apr.contracts.model import Contract, all_contracts

DIALECT = "spark"
SQL_TYPES = {
    "string": "string",
    "long": "bigint",
    "double": "double",
    "date": "date",
    "timestamp": "timestamp",
    "boolean": "boolean",
}
WRITE_NODES = (
    exp.Insert,
    exp.Delete,
    exp.Update,
    exp.Create,
    exp.Drop,
    exp.Merge,
    exp.Alter,
    exp.Command,
    exp.TruncateTable,
    exp.Use,
    exp.Set,
    exp.Cache,
    exp.Uncache,
    exp.Copy,
)


class Refused(Exception):
    def __init__(self, rule: str, message: str, status: int = 400):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.status = status


@dataclass
class TableScan:
    table: str
    snapshot_id: int | None
    columns: set[str]
    row_filter: BooleanExpression
    est_bytes: int = 0
    est_rows: int = 0
    files: int = 0
    files_total: int = 0


@dataclass
class Plan:
    user: str
    role: str
    sql: str  # as submitted
    logical: str  # qualified, normalised (Spark SQL)
    scans: list[TableScan]
    est_bytes: int
    lane: str  # fast | spark
    row_limit: int
    limit_added: bool
    masked: list[str] = field(default_factory=list)
    physical: dict[str, str] = field(default_factory=dict)  # lane -> SQL
    fingerprint: str = ""

    @property
    def snapshots(self) -> dict[str, int | None]:
        return {s.table: s.snapshot_id for s in self.scans}


class Policy:
    def __init__(self, roles_cfg: dict | None = None, contracts: dict[str, Contract] | None = None):
        self.cfg = roles_cfg or conf.read("roles")
        self.contracts = contracts or all_contracts()

    def role_of(self, user: str) -> str:
        role = self.cfg["users"].get(user)
        if role is None:
            raise Refused("unknown_user", f"{user!r} is not a known user; ask #data-platform for access.", 403)
        return role

    def treatment(self, role: str, cls: str) -> str:
        r = self.cfg["roles"][role]
        if cls in r.get("clear", []):
            return "clear"
        if cls in r.get("masked", []):
            return "masked"
        return "denied"

    def visible_schema(self, role: str) -> dict[str, dict[str, dict[str, str]]]:
        out: dict[str, dict[str, dict[str, str]]] = {}
        for c in self.contracts.values():
            cols = {
                col.name: SQL_TYPES[col.type]
                for col in c.columns
                if self.treatment(role, col.classification) != "denied"
            }
            out.setdefault(c.namespace, {})[c.short] = cols
        return out

    def mask_expr(self, role: str, c: Contract, column: str) -> exp.Expression:
        col = c.column(column)
        assert col is not None
        ident = exp.column(column)
        if self.treatment(role, col.classification) == "clear":
            return ident
        how = self.cfg["masking"][col.type]
        if how == "sha256":
            e = sqlglot.parse_one(f"SHA2(CAST({column} AS STRING), 256)", read=DIALECT)
        elif how == "year":
            e = sqlglot.parse_one(f"MAKE_DATE(YEAR({column}), 1, 1)", read=DIALECT)
        elif how == "day":
            e = sqlglot.parse_one(f"CAST(DATE_TRUNC('DAY', {column}) AS TIMESTAMP)", read=DIALECT)
        else:
            e = sqlglot.parse_one(f"CAST(NULL AS {SQL_TYPES[col.type].upper()})", read=DIALECT)
        return exp.alias_(e, column)


# ---- helpers -----------------------------------------------------------------------------


def _table_name(t: exp.Table) -> str:
    parts = [p.lower() for p in (t.catalog, t.db, t.name) if p]  # Spark identifiers are case-insensitive
    lake = conf.read("platform")["gateway"]["spark"]["catalog"]
    if len(parts) == 3 and parts[0] == lake:
        parts = parts[1:]
    return ".".join(parts)


def _conjuncts(e: exp.Expression | None) -> list[exp.Expression]:
    if e is None:
        return []
    if isinstance(e, exp.And):
        return _conjuncts(e.left) + _conjuncts(e.right)
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    return [e]


def _constant(e: exp.Expression) -> Any:
    """The value of a constant expression (a literal, a cast literal, current_date() - 7 ...)."""
    if e.find(exp.Column):
        raise ValueError("not constant")
    if isinstance(e, exp.Literal):
        return e.this if e.is_string else float(e.this) if "." in e.this else int(e.this)
    if isinstance(e, exp.Cast) and isinstance(e.this, exp.Literal) and e.this.is_string and e.to.this in _TEMPORAL:
        # timestamps in Spark SQL are session-local (UTC here); no engine is needed for a literal
        v = e.this.this
        return dt.date.fromisoformat(v) if e.to.this == exp.DataType.Type.DATE else dt.datetime.fromisoformat(v)
    sql = exp.select(e.copy()).sql("duckdb")
    with _CONST_LOCK:
        row = _const_con().execute(sql).fetchone()
    return row[0] if row else None


_TEMPORAL = {
    exp.DataType.Type.DATE,
    exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.TIMESTAMPNTZ,
    exp.DataType.Type.TIMESTAMPLTZ,
    exp.DataType.Type.DATETIME,
}
_CONST_LOCK = threading.Lock()
_CONST: list[duckdb.DuckDBPyConnection] = []


def _const_con() -> duckdb.DuckDBPyConnection:
    if not _CONST:
        con = duckdb.connect()
        con.execute("SET TimeZone = 'UTC'")
        _CONST.append(con)
    return _CONST[0]


def _iceberg_value(v: Any, col_type: str) -> Any:
    if isinstance(v, dt.datetime):
        v = v.replace(tzinfo=None)
        return v.isoformat() if col_type == "timestamp" else v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat() if col_type == "date" else dt.datetime.combine(v, dt.time()).isoformat()
    if col_type in ("timestamp", "date") and isinstance(v, str):
        return (
            v.replace(" ", "T")
            if col_type == "timestamp" and len(v) > 10
            else (v if col_type == "date" else f"{v[:10]}T00:00:00")
        )
    return v


OPS = {exp.GTE: GreaterThanOrEqual, exp.GT: GreaterThan, exp.LTE: LessThanOrEqual, exp.LT: LessThan, exp.EQ: EqualTo}
FLIP = {exp.GTE: exp.LTE, exp.GT: exp.LT, exp.LTE: exp.GTE, exp.LT: exp.GT, exp.EQ: exp.EQ}


def _predicates(
    conds: list[exp.Expression], alias: str, c: Contract, clear: set[str]
) -> tuple[list[BooleanExpression], set[str]]:
    """Iceberg filters for a table reference from its constant comparisons, and which of its
    columns are constrained at all. Only clear columns are pushed to storage: pushing a filter
    on a masked column would let a query test clear values it may not see."""
    preds: list[BooleanExpression] = []
    constrained: set[str] = set()

    def col_of(e: exp.Expression) -> str | None:
        if isinstance(e, exp.Cast):
            e = e.this
        if isinstance(e, exp.Column) and e.table == alias and c.column(e.name):
            return e.name
        return None

    for cond in conds:
        for k in OPS:
            if isinstance(cond, k):
                left, right, op = cond.left, cond.right, k
                name = col_of(left)
                if name is None:
                    name, left, right, op = col_of(right), right, left, FLIP[k]
                if name is None:
                    break
                try:
                    v = _constant(right)
                except Exception:
                    break
                constrained.add(name)
                if name in clear and v is not None:
                    col = c.column(name)
                    assert col is not None
                    preds.append(OPS[op](name, _iceberg_value(v, col.type)))
                break
        if isinstance(cond, exp.Between):
            name = col_of(cond.this)
            if name:
                try:
                    lo, hi = _constant(cond.args["low"]), _constant(cond.args["high"])
                except Exception:
                    continue
                constrained.add(name)
                if name in clear:
                    col = c.column(name)
                    assert col is not None
                    preds.append(GreaterThanOrEqual(name, _iceberg_value(lo, col.type)))
                    preds.append(LessThanOrEqual(name, _iceberg_value(hi, col.type)))
        elif isinstance(cond, exp.In) and not cond.args.get("query"):
            name = col_of(cond.this)
            if name:
                try:
                    vals = [_constant(x) for x in cond.expressions]
                except Exception:
                    continue
                constrained.add(name)
                if name in clear:
                    col = c.column(name)
                    assert col is not None
                    preds.append(In(name, [_iceberg_value(x, col.type) for x in vals]))
    return preds, constrained


def _and(preds: list[BooleanExpression]) -> BooleanExpression:
    out: BooleanExpression = AlwaysTrue()
    for p in preds:
        out = p if isinstance(out, AlwaysTrue) else And(out, p)
    return out


def _scope_conditions(scope: Scope) -> list[exp.Expression]:
    sel = scope.expression
    conds = _conjuncts(sel.args.get("where").this) if sel.args.get("where") else []
    for j in sel.args.get("joins") or []:
        conds += _conjuncts(j.args.get("on"))
    return conds


# ---- the planner -------------------------------------------------------------------------


class Planner:
    """`enforce=False` plans the way a cluster without the gateway would run a query (no
    partition rule, no scan limit) and `size_factor` scales estimates to production volume;
    both exist for the workload simulation's baseline and are never set in the service."""

    def __init__(
        self,
        policy: Policy | None = None,
        catalog=None,
        platform: dict | None = None,
        enforce: bool = True,
        size_factor: float | dict[str, float] = 1.0,
    ):
        self.policy = policy or Policy()
        self._catalog = catalog
        self.cfg = (platform or conf.read("platform"))["gateway"]
        self.enforce = enforce
        self.size_factor = size_factor
        self._tables: dict[str, tuple[float, Any]] = {}
        self.catalog_loads = 0

    def load_table(self, name: str):
        """Table metadata, reused for catalog_cache_s: a loaded snapshot is immutable, and the
        cache only delays seeing a new one by that long (results stay keyed by snapshot)."""
        ttl = float(self.cfg.get("catalog_cache_s", 0))
        now = time.monotonic()
        hit = self._tables.get(name)
        if hit and now - hit[0] < ttl:
            return hit[1]
        t = self.catalog.load_table(name)
        self.catalog_loads += 1
        self._tables[name] = (now, t)
        return t

    def forget(self) -> None:
        self._tables.clear()

    @property
    def catalog(self):
        if self._catalog is None:
            from apr.lake.catalog import catalog

            self._catalog = catalog()
        return self._catalog

    def parse(self, sql: str) -> exp.Expression:
        try:
            stmts = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
        except ParseError as e:
            raise Refused("parse_error", f"The query is not valid Spark SQL: {str(e).splitlines()[0]}") from e
        if len(stmts) != 1:
            raise Refused("single_statement", "Send one statement at a time.")
        tree = stmts[0]
        if not isinstance(tree, exp.Query) or tree.find(*WRITE_NODES):
            raise Refused(
                "read_only",
                "The gateway runs read-only queries. Writes go through a data product "
                "pipeline (`apr new`), not through ad-hoc SQL.",
            )
        return tree

    def plan(self, sql: str, user: str) -> Plan:
        role = self.policy.role_of(user)
        tree = self.parse(sql)
        ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        referenced: dict[str, Contract] = {}
        for t in tree.find_all(exp.Table):
            if not isinstance(t.this, exp.Identifier):
                raise Refused(
                    "table_function",
                    "Only data product tables can be read; table functions and file paths are not allowed.",
                )
            if not t.db and t.name in ctes:
                continue
            name = _table_name(t)
            c = self.policy.contracts.get(name)
            if c is None:
                known = ", ".join(sorted(self.policy.contracts))
                raise Refused("unknown_table", f"{name} is not a data product. Known tables: {known}.")
            referenced[name] = c
        # resolve every column against what this role may see
        try:
            tree = qualify(
                tree,
                schema=self.policy.visible_schema(role),
                dialect=DIALECT,
                validate_qualify_columns=True,
                quote_identifiers=False,
            )
        except OptimizeError as e:
            found = re.search(r"Column '([^']+)'|Unknown column: (\w+)", str(e))
            missing = (found.group(1) or found.group(2)) if found else ""
            for c in referenced.values():
                col = c.column(missing)
                if col is not None and self.policy.treatment(role, col.classification) == "denied":
                    raise Refused(
                        "denied_column",
                        f"{user} ({role}) may not read {c.table}.{col.name}: it is "
                        f"classified {col.classification}. Ask the owner "
                        f"({c.owner.contact}) or use an aggregate product.",
                        403,
                    ) from e
            raise Refused("unknown_column", f"Could not resolve column {missing!r}: {str(e).splitlines()[0]}") from e

        scans: list[TableScan] = []
        masked: set[str] = set()
        table_nodes: list[tuple[exp.Table, TableScan, Contract]] = []
        for scope in traverse_scope(tree):
            conds = _scope_conditions(scope)
            for alias, src in scope.sources.items():
                if not isinstance(src, exp.Table) or (not src.db and src.name in ctes):
                    continue
                c = referenced[_table_name(src)]
                used = {col.name for col in scope.columns if col.table == alias}
                if not used:
                    used = {c.columns[0].name}  # count(*): read the narrowest thing
                clear = {col.name for col in c.columns if self.policy.treatment(role, col.classification) == "clear"}
                masked |= {f"{c.table}.{u}" for u in used if u not in clear}
                preds, constrained = _predicates(conds, alias, c, clear)
                if c.require_partition_filter and self.enforce:
                    sources = {p.source for p in c.partitioning}
                    if not sources & constrained:
                        src_col = c.partitioning[0].source
                        raise Refused(
                            "partition_filter_required",
                            f"{c.table} is large and partitioned by {src_col}; filter on it, for example "
                            f"WHERE {alias}.{src_col} >= DATE '2026-09-01'. Daily aggregates are in "
                            f"{', '.join(x.table for x in self.policy.contracts.values() if c.name in x.upstream) or 'the marts'}.",
                        )
                scan = TableScan(c.table, None, used, _and(preds))
                scans.append(scan)
                table_nodes.append((src, scan, c))

        self.estimate(scans)
        est = sum(s.est_bytes for s in scans)
        limit_bytes = int(self.policy.cfg["roles"][role]["max_scan_bytes"])
        if est > limit_bytes and self.enforce:
            raise Refused(
                "scan_limit",
                f"This query would scan about {est / 2**30:.1f} GiB; the {role} limit is "
                f"{limit_bytes / 2**30:.0f} GiB. Narrow the date range or use a mart.",
            )
        lane = "fast" if est <= int(self.cfg["fast_lane_max_bytes"]) else "spark"

        # cap rows returned
        cap = int(self.cfg["interactive_row_limit"])
        limit_added = False
        lim = tree.args.get("limit")
        current = None
        if lim is not None:
            try:
                current = int(lim.expression.name)
            except (ValueError, AttributeError):
                current = None
        if (current is None or current > cap) and self.enforce:
            tree = tree.limit(cap, copy=False)
            limit_added = True

        logical = tree.sql(DIALECT)
        physical = {
            "fast": self._rewrite(tree, table_nodes, role, "fast").sql("duckdb"),
            "spark": self._rewrite(tree, table_nodes, role, "spark").sql(DIALECT),
        }
        fp = hashlib.sha256(f"{role}\x00{physical['spark']}".encode()).hexdigest()
        return Plan(user, role, sql, logical, scans, est, lane, cap, limit_added, sorted(masked), physical, fp)

    def _rewrite(
        self, tree: exp.Expression, nodes: list[tuple[exp.Table, TableScan, Contract]], role: str, lane: str
    ) -> exp.Expression:
        """Replace each table with a sub-query over the role's columns, masked, at a pinned snapshot."""
        replacements = {id(t): (s, c) for t, s, c in nodes}
        spark_catalog = self.cfg["spark"]["catalog"]

        def swap(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Table) and id(node) in replacements:
                s, c = replacements[id(node)]
                cols = [
                    self.policy.mask_expr(role, c, name)
                    for name in [col.name for col in c.columns if col.name in s.columns]
                ]
                if lane == "fast":
                    src = exp.to_table(scan_view(s))
                else:
                    src = exp.Table(
                        this=exp.to_identifier(c.short),
                        db=exp.to_identifier(c.namespace),
                        catalog=exp.to_identifier(spark_catalog),
                    )
                    if s.snapshot_id is not None:
                        src.set(
                            "version",
                            exp.Version(this="VERSION", kind="AS OF", expression=exp.Literal.number(s.snapshot_id)),
                        )
                inner = exp.select(*cols).from_(src)
                return exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(node.alias_or_name)))
            return node

        return _transform_by_identity(tree, swap)

    def estimate(self, scans: list[TableScan]) -> None:
        """Bytes and rows a scan will read, from Iceberg manifests: partition pruning and column
        min/max metrics through plan_files, and per-column sizes for the projected columns."""
        for s in scans:
            t = self.load_table(s.table)
            snap = t.current_snapshot()
            s.snapshot_id = snap.snapshot_id if snap else None
            if snap is None:
                continue
            ids = {f.name: f.field_id for f in t.schema().fields}
            wanted = [ids[c] for c in s.columns if c in ids]
            total_files = int(snap.summary.get("total-data-files", "0")) if snap.summary else 0
            files = 0
            for task in t.scan(row_filter=s.row_filter, snapshot_id=snap.snapshot_id).plan_files():
                f = task.file
                sizes = f.column_sizes or {}
                s.est_bytes += sum(sizes.get(i, 0) for i in wanted) if sizes else f.file_size_in_bytes
                s.est_rows += f.record_count
                files += 1
            s.files, s.files_total = files, total_files
            f = (
                self.size_factor.get(s.table, self.size_factor.get("default", 1.0))
                if isinstance(self.size_factor, dict)
                else self.size_factor
            )
            s.est_bytes = int(s.est_bytes * f)
            s.est_rows = int(s.est_rows * f)


def _transform_by_identity(tree: exp.Expression, fn) -> exp.Expression:
    """sqlglot's transform copies before visiting, which loses node identity; walk a copy
    while visiting the original nodes in the same order."""
    copy = tree.copy()
    originals = list(tree.walk())
    copies = list(copy.walk())
    for orig, cp in zip(originals, copies):
        new = fn(orig)
        if new is not orig:
            cp.replace(new)
    return copy


def scan_view(s: TableScan) -> str:
    """Name of the DuckDB view a fast-lane scan is registered as (one per table, snapshot, columns, filter)."""
    key = hashlib.sha1(f"{s.table}|{s.snapshot_id}|{sorted(s.columns)}|{s.row_filter}".encode()).hexdigest()[:10]
    return f"{s.table.replace('.', '__')}__{key}"


def any_of(filters: list[BooleanExpression]) -> BooleanExpression:
    if any(isinstance(f, AlwaysTrue) for f in filters):
        return AlwaysTrue()
    out = filters[0]
    for f in filters[1:]:
        out = Or(out, f)
    return out
