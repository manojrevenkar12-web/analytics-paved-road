# 3. Write-audit-publish on Iceberg branches

Status: accepted

## Context

A bad batch that lands on `main` is read by dashboards and models within minutes, and fixing it
afterwards means finding everyone who read it.

## Decision

Every batch is written to a branch `audit-<day>` created from `main`, checked against its
contract (schema, not-null, accepted values, uniqueness, positive amounts, row counts from
snapshot metadata), and only then published by moving `main` to the audited snapshot. A failing
batch leaves `main` untouched and is quarantined with its failed checks.

## Consequences

- Readers never see unaudited data; they see yesterday's data until a fixed batch arrives
  (the `bad_batch` drill).
- Checks run on the batch, not the table, so their cost does not grow with history.
