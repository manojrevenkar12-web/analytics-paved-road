# 1. The gateway enforces access by rewriting the query

Status: accepted

## Context

Two engines read the lake (DuckDB in the gateway, Spark through Kyuubi), and more will come
(Trino, a notebook's own DuckDB). Each has its own access model; keeping column masks in sync
across them is where leaks happen. Iceberg itself has no column-level access control.

## Decision

The gateway resolves every column against the schema the role may see and rewrites each table
reference into a sub-query that already applies the role's masks, at a pinned snapshot. The
engines receive a query that can only read what the role may read. Filters on masked columns are
never pushed to storage, so a query cannot probe clear values through pruning.

## Consequences

- One policy, in `config/roles.yaml`, tested once (`tests/test_policy.py`, the blind access set).
- Engines can run with a read-only service identity on the whole lake (`deploy/terraform`).
- Direct engine access must be closed off (network policy, no direct Kyuubi endpoint for people).
- Masking costs a little: hashed columns are computed per query.
