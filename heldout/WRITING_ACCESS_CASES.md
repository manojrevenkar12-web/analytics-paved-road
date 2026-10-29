# Writing held-out access cases for the query gateway

You are writing a blind test of the gateway's access rules. Write it from
**`docs/ACCESS.md` and `config/products/*.yaml` only** (and this guide). Do not read anything
under `src/`, `tests/`, other files in `config/`, or earlier case sets.

Each case is a Spark SQL query, the user who sends it, and what the rules in `docs/ACCESS.md`
say must happen. Think like a curious analyst, a careless product manager, a compliance
investigator, an n8n workflow, and someone trying to get around the rules.

## File

`heldout/access/cases.yaml`:

```yaml
cases:
  - id: a01
    user: pia
    sql: "SELECT country, COUNT(*) FROM raw.customers GROUP BY country"
    expect: allow
    masked: []                    # for allow: fully qualified columns that must come back masked
    note: "aggregates over public columns are fine for a product manager"
  - id: a02
    user: pia
    sql: "SELECT email FROM raw.customers"
    expect: refuse
    rule: denied_column           # for refuse: the rule name from docs/ACCESS.md
    note: "PII is not visible to product managers at all"
```

Write `masked` as `<table>.<column>` (e.g. `raw.customers.email`), listing every masked column
the query reads. For `refuse`, `rule` is one of: `read_only`, `single_statement`,
`table_function`, `unknown_table`, `unknown_column`, `unknown_user`, `denied_column`,
`partition_filter_required`, `scan_limit`, `parse_error`.

## What to write

40 cases, about half allowed and half refused, covering:

- every role, and an unknown user;
- each classification in clear, masked and denied, including through `SELECT *`, sub-queries,
  CTEs, joins, `WHERE`, `GROUP BY`, `ORDER BY` and functions applied to a column;
- the partition filter in its accepted forms (comparisons, `BETWEEN`, `IN`, an expression on
  `current_date()`, a cast of the column, in the `ON` of a join) and in forms that do not count
  (a filter on another column only, no filter);
- writes, several statements, table functions and file paths, system tables, unknown tables,
  case and quoting of names, and the `lake.` prefix;
- attempts to get around the rules (renaming a column in a sub-query, a CTE over a denied
  column, `UNION` with a denied column, a comment hiding a second statement).

Do not write cases about scan limits or row caps (they depend on data volume), or about
latency. The set is run once, and that first run is kept. Nobody changes the gateway to pass it.
