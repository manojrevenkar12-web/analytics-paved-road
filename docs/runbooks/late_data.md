# Runbook: a data product is late

Alert: `APR_ProductLate{product=...}`. The product has no data for yesterday after the time in its
contract (`freshness.expected_by`). Marts page their owner; raw products open a ticket.

1. Upstream? A mart's DAG is scheduled on its upstream assets; find the upstream that has not
   updated (`ingest__<name>` runs, `reports` of the run log in the warehouse's `runs.jsonl`).
2. Quarantined? A batch that failed its contract checks is not published; the run log says which
   check failed. Ask the source owner for a corrected batch.
3. Consumers keep seeing the previous day; the console and the API show "data through".
4. When the batch lands, the DAG runs by itself; the alert resolves when the mart is published.
