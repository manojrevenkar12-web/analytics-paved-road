"""Run in the Airflow environment: .venv-airflow/bin/pytest airflow/tests"""

from pathlib import Path

import yaml
from airflow.models import DagBag

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "config" / "products").glob("*.yaml"))]


def bag() -> DagBag:
    return DagBag(dag_folder=str(ROOT / "airflow" / "dags"), include_examples=False)


def test_dags_import_cleanly():
    b = bag()
    assert b.import_errors == {}


def test_one_dag_per_product_and_maintenance():
    ids = set(bag().dag_ids)
    want = {f"{'ingest' if c['build']['kind'] == 'ingest' else 'build'}__{c['name']}" for c in CONTRACTS}
    assert ids == want | {"maintain__lake"}


def test_marts_are_scheduled_on_their_upstream_assets():
    b = bag()
    for c in CONTRACTS:
        if c["build"]["kind"] != "dbt":
            continue
        dag = b.get_dag(f"build__{c['name']}")
        found = list(dag.timetable.asset_condition.iter_assets())
        names = {(x[1] if isinstance(x, tuple) else x).name for x in found}
        ups = {next(x["table"] for x in CONTRACTS if x["name"] == u) for u in c["upstream"]}
        assert names == ups, (c["name"], names)
        assert [t.task_id for t in dag.tasks] == ["dbt_build", "publish"]
        assert dag.get_task("publish").outlets[0].name == c["table"]


def test_owners_come_from_the_contract():
    b = bag()
    for c in CONTRACTS:
        dag_id = f"{'ingest' if c['build']['kind'] == 'ingest' else 'build'}__{c['name']}"
        for t in b.get_dag(dag_id).tasks:
            assert t.owner == c["owner"]["team"]
