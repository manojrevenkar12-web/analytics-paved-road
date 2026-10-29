# 2. Route by bytes estimated from Iceberg metadata

Status: accepted

## Context

Most analytical queries are small (a mart, a few days of a fact table), and a Spark session
costs seconds before it reads anything. A few are large and need a cluster.

## Decision

Estimate the bytes a query reads from Iceberg manifests (`plan_files` after partition and
statistics pruning, summed over the projected columns' sizes) and run it in-process on DuckDB
up to 256 MiB, on Spark through Kyuubi above. The same estimate enforces each role's scan limit.

## Consequences

- Planning reads manifests: tables need compaction (`apr maintain`; 120 daily appends made
  planning 7x slower before compaction).
- The estimate is exact for the fast lane (it reads exactly the planned files and columns) and
  an upper bound for Spark, which may prune further at runtime.
- The threshold is a setting (`config/platform.yaml`); the workload simulation shows its effect.
