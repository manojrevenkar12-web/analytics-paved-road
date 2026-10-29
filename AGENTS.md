# Working in this repository

## Fast feedback

- `pytest -q` (about a minute): builds an 8-day lake in a temp directory, then tests the contracts,
  ingestion, the planner's rules, the gateway, the semantic layer, the question parser, SLO rules,
  the API, the workload simulator's shortcuts, shadow replays and the real drills.
- `ruff check src tests airflow && ruff format --check src tests airflow && mypy`
- `apr lint` (contracts), `apr generate && git diff --exit-code` (generated files are current).
- `cd console && npm test`, `cd integrations/n8n-nodes-paved-road && npm test`.
- Airflow: `.venv-airflow/bin/pytest airflow/tests` (separate environment).
- `apr build` builds the full 120-day lake (about 3 minutes); `apr simulate` (about 10 minutes) and
  `apr drill` regenerate `reports/`.

## Invariants (each has a test)

- **Access is enforced by rewriting, not by trusting an engine.** Every table reference becomes a
  sub-query over exactly the columns the role may see, masked columns already masked, at a pinned
  snapshot. Filters on masked columns are never pushed to storage (`tests/test_policy.py`).
- **Read-only, data products only.** The gateway runs one SELECT over contract tables; writes go
  through pipelines.
- **Results are keyed by snapshot.** The cache key is the physical query, which names its
  snapshots and masks; a new snapshot can never be answered from an old result (`tests/test_gateway.py`).
- **Write-audit-publish.** A batch reaches `main` only after its contract checks pass on an audit
  branch (`tests/test_ingest.py`, the `bad_batch` drill).
- **Models parse, code computes.** The question parser (rules or Claude) only produces a metric
  query; the answer text is written by code from the result rows (`tests/test_ask.py`).
- **Settings in `config/`**, never in code. Generated files (`transform/models/marts/_contracts.yml`,
  `deploy/prometheus/**`, `deploy/grafana/**`, `docs/METRICS.md`) come from `apr generate`.
- **Reports are generated** (`reports/`), not written by hand; modelled numbers say so.

## Held-out sets

`heldout/ask/questions.yaml` and `heldout/access/cases.yaml` were written by a separate agent from
`docs/METRICS.md`, `docs/ACCESS.md`, the contracts and `heldout/WRITING_*.md` only. Their first runs
are in `reports/heldout/<set>/first_run.json`, pinned by sha256 in `tests/test_heldout.py`, and
are never rewritten. Never change the parser or the planner to pass them. If you study their
failures, write `regression` to `heldout/<set>/STATUS` and have someone who has not seen the code
write a new set from the guide.
