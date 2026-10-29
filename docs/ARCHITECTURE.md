# Architecture

```
 people, notebooks, BI, n8n            console (React)        apr CLI
            │                                │                    │
            └──────────────┬─────────────────┴────────────────────┘
                           ▼  X-User from the ingress (OIDC)
                 ┌──────────────────────┐
                 │   query gateway      │  parse (sqlglot, Spark SQL) → policy → rewrite
                 │   (FastAPI, k8s)     │  estimate from Iceberg manifests → lane → admit
                 │                      │  snapshot-keyed cache, metrics, audit log
                 └───┬──────────────┬───┘
            ≤256 MiB │              │ bigger
                     ▼              ▼
           DuckDB in the pod   Kyuubi → Spark (Iceberg runtime)
                     │              │
                     └──────┬───────┘
                            ▼
             Iceberg REST catalog (Glue on AWS) + S3 (MinIO in kind)
                            ▲
            write-audit-publish (audit branch → checks → move main)
                            │
     Airflow DAG factory ── ingest raw products ── dbt marts ── publish ── maintain
                            ▲
                config/products/*.yaml (contracts)  ──►  dbt tests, DAGs, dashboard, alert rules
```

## The request path

1. **Identity.** The ingress (oauth2-proxy) authenticates and sets `X-User`. The gateway looks
   the role up in `config/roles.yaml`; it never accepts a role from the client.
2. **Parse.** One Spark SQL statement, read-only, data product tables only.
3. **Policy.** Columns are resolved with sqlglot's `qualify` against the schema *the role may
   see*, so a denied column is an unresolvable name and the query is refused with the owner to
   ask. `SELECT *` expands to visible columns only.
4. **Rewrite.** Each table becomes a sub-query selecting the role's columns, masked ones masked
   (SHA-256, year, day, null by type), pinned with `VERSION AS OF <snapshot>` on Spark or a
   registered Arrow scan on DuckDB. Access does not depend on either engine's permissions.
5. **Estimate.** Constant predicates on clear columns become PyIceberg filters;
   `scan().plan_files()` prunes partitions and min/max statistics; the estimate is the sum of
   the projected columns' sizes in the files kept. Tables that must be filtered on their
   partition column are refused without one.
6. **Lane and admission.** Up to 256 MiB runs on DuckDB in the pod, bigger on Spark via
   Kyuubi. Slots per lane and per user; a full lane answers 429 after its queue timeout.
   If Kyuubi is down, queries up to 1 GiB run degraded on DuckDB.
7. **Cache.** Keyed by the physical SQL (snapshots and masks included), LRU by bytes.
8. **Meter and audit.** Prometheus metrics (the SLIs) and an append-only audit line per request.

## The paved road for producers

A team writes `config/products/<name>.yaml` (or `apr new`). From it come the Iceberg table and
partition spec, the dbt model contract and tests (`transform/models/marts/_contracts.yml`), the
Airflow DAG (asset-scheduled on its upstream products), a dashboard row and a freshness alert
to the owner's channel. `apr lint` runs in CI: personal data must be classified, a mart cannot
promise freshness before its upstream, consumers must exist, and against the base branch a
removed or retyped column or a lowered classification is a breaking change.

## Reliability

SLIs are the gateway's counters; SLOs live in `config/slo.yaml` and are generated into
recording rules, multi-window multi-burn-rate alerts (page at 14.4x over 1 h and 5 min, 6x over
6 h and 30 min; ticket at 3x and 1x) and promtool unit tests. `apr budget` enforces the error
budget policy in the deploy job. Drills inject the failures the platform should survive and
write a postmortem-style timeline; shadow replays check a policy or engine change against the
audit log before it ships.
