# Querying through the gateway

Every query to the lake goes through the query gateway: the console's SQL tab, `apr query`,
`POST /api/query`, the n8n node, and the metrics layer. You write **Spark SQL**. The gateway
checks the query before anything runs, picks where it runs, and tells you why when it refuses.

## Who sees what

Every column in a data product contract (`config/products/*.yaml`) has a classification.
Your role decides, per classification, whether you see a column in clear, masked, or not at all.

| Role | Users | Clear | Masked | Not at all | Largest scan per query |
|---|---|---|---|---|---|
| analyst | `ana`, `ben` | public, internal, financial | pii_quasi, pii_direct | – | 20 GiB |
| product_manager | `pia` | public, internal, financial | – | pii_quasi, pii_direct | 5 GiB |
| compliance | `carl` | everything | – | – | 50 GiB |
| automation | `n8n` | public, internal | – | financial, pii_quasi, pii_direct | 1 GiB |

Users not in this table are refused (`unknown_user`).

**Masked** columns are still there, changed so they keep working for counting and joining but
reveal nothing: text (names, e-mails, IBANs) becomes its SHA-256 hash in hex; a date (birth
date) becomes the first day of its year; a timestamp becomes midnight of its day; numbers
become null. Because the mask is applied before your query sees the data, a filter on a masked
column compares against the masked value: `WHERE email = 'anna@example.org'` matches nothing
for an analyst, it does not tell you whether that e-mail exists.

**Not at all**: naming such a column anywhere in the query (select list, filter, join, group,
order, a sub-query or CTE) refuses the whole query (`denied_column`, HTTP 403). `SELECT *`
does not refuse: it simply leaves those columns out.

## What a query may do

- **Read only.** One `SELECT` (with `WITH`, joins, sub-queries, `UNION`) per request. Anything
  that writes, creates, drops, caches or sets a session option is refused (`read_only`).
  Several statements in one request are refused (`single_statement`).
- **Data products only.** Tables are named `raw.<name>` or `mart.<name>` (optionally prefixed
  with the catalog name `lake.`); names are case-insensitive. Any other table, system tables,
  and table functions or file paths (`read_csv(...)`, `read_parquet(...)`) are refused
  (`unknown_table` or `table_function`).
- **Big tables need a partition filter.** `raw.trades` (partitioned by day of `executed_at`) and
  `raw.cash_movements` (by day of `booked_at`) must be filtered on that column with a constant:
  `>=`, `>`, `<`, `<=`, `=`, `BETWEEN`, or `IN`, against a date, a timestamp, or an expression
  such as `current_date() - INTERVAL 7 DAYS`. A cast of the column (`CAST(executed_at AS DATE)`)
  counts. The filter may be in the `WHERE` of the query block that reads the table, or in the
  `ON` of the join that brings it in. Without one the query is refused
  (`partition_filter_required`, HTTP 400), with an example of the filter to add.
- **Scan limits.** The gateway estimates the bytes a query will read from Iceberg metadata
  (partition pruning, column statistics, only the columns used). Above your role's limit the
  query is refused (`scan_limit`).
- **Row cap.** At most 10,000 rows come back. A query without `LIMIT`, or with a bigger one, is
  capped at 10,000 and the response says so (`truncated: true`). Aggregate instead.

Unknown columns are refused with `unknown_column`; SQL that does not parse with `parse_error`.

## Where it runs

Queries estimated at up to 256 MiB run in the gateway on DuckDB (the fast lane, usually well
under a second). Bigger ones go to Spark through Kyuubi (the Spark lane). If the Spark lane is
down, queries up to 1 GiB run on the fast lane instead; bigger ones get HTTP 503
(`lane_unavailable`). When a lane is full, a query waits up to 5 s (fast) or 60 s (Spark) for
a slot, then gets HTTP 429 (`queue_full`). Each user has at most 2 queries running at a time
(`user_concurrency`, HTTP 429).

Every query reads a pinned Iceberg snapshot of each table. Results are cached by the exact
snapshots and the masking applied, so a repeated query is answered from the cache until new
data lands, and never from an older snapshot.

## Dry run

`POST /api/query` with `"dry_run": true` (the console shows it as you type) returns the
decision, the estimated bytes and rows per table, files read out of the table's total, the
lane, masked columns, and the SQL that would run, without running anything.

## Audit

Every request, answered or refused, is in the audit log with its user, role, SQL, decision,
snapshots and cost. You see your own entries; compliance sees everyone's.
