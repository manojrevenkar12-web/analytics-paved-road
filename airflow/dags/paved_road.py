"""The DAG factory: one DAG per data product, generated from config/products/*.yaml.

Raw products are ingested each morning (write-audit-publish into Iceberg) and update an Airflow
asset; a mart is scheduled on the assets of its upstream products, so it runs as soon as the
last of them lands instead of at a guessed time. A nightly DAG compacts tables and expires
snapshots. Freshness objectives are watched by Prometheus (APR_ProductLate), not by Airflow.

Tasks run `apr` in a pod on Kubernetes (APR_AIRFLOW_EXECUTOR=kubernetes) or as a local
command (the default, for development and the DagBag test).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from airflow.sdk import DAG, Asset

ROOT = Path(os.environ.get("APR_ROOT", Path(__file__).resolve().parents[2]))
IMAGE = os.environ.get("APR_IMAGE", "ghcr.io/manojrevenkar12-web/analytics-paved-road:latest")
ON_K8S = os.environ.get("APR_AIRFLOW_EXECUTOR", "local") == "kubernetes"
START = datetime(2026, 6, 1)


def contracts() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "config" / "products").glob("*.yaml"))]


def asset(c: dict) -> Asset:
    return Asset(
        name=c["table"], uri=f"iceberg://lake/{c['table'].replace('.', '/')}", extra={"owner": c["owner"]["team"]}
    )


def task(task_id: str, args: list[str], c: dict, outlets: list[Asset] | None = None):
    owner = c["owner"]["team"]
    if ON_K8S:
        from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

        return KubernetesPodOperator(
            task_id=task_id,
            name=f"apr-{task_id}".replace("_", "-"),
            namespace="analytics",
            image=IMAGE,
            cmds=["apr"],
            arguments=args,
            env_from=[{"configMapRef": {"name": "apr-lake"}}],
            service_account_name="apr-pipelines",
            get_logs=True,
            on_finish_action="delete_succeeded_pod",
            owner=owner,
            outlets=outlets or [],
            retries=2,
            retry_delay=timedelta(minutes=5),
        )
    from airflow.providers.standard.operators.bash import BashOperator

    return BashOperator(
        task_id=task_id,
        bash_command="apr " + " ".join(args),
        owner=owner,
        outlets=outlets or [],
        retries=2,
        retry_delay=timedelta(minutes=5),
        cwd=str(ROOT),
    )


def build() -> dict[str, DAG]:
    all_c = contracts()
    by_name = {c["name"]: c for c in all_c}
    dags: dict[str, DAG] = {}
    for c in all_c:
        tags = ["paved-road", c["layer"], c["domain"], c["owner"]["team"]]
        doc = (
            f"{c['description']}\n\nOwner: {c['owner']['team']} ({c['owner']['contact']}). "
            f"Due by {c['freshness']['expected_by']} UTC, objective {c['freshness']['objective']:.0%}."
        )
        if c["build"]["kind"] == "ingest":
            h = 4
            with DAG(
                dag_id=f"ingest__{c['name']}",
                schedule=f"0 {h} * * *",
                start_date=START,
                catchup=False,
                tags=tags,
                doc_md=doc,
                max_active_runs=1,
            ) as dag:
                task(
                    "ingest",
                    ["ingest", "--table", c["table"], "--day", "{{ macros.ds_add(ds, 0) }}"],
                    c,
                    outlets=[asset(c)],
                )
        else:
            ups = [asset(by_name[u]) for u in c["upstream"]]
            with DAG(
                dag_id=f"build__{c['name']}",
                schedule=ups,
                start_date=START,
                catchup=False,
                tags=tags,
                doc_md=doc,
                max_active_runs=1,
            ) as dag:
                dbt = task("dbt_build", ["dbt", "build", "--select", f"+{c['build']['model']}"], c)
                pub = task(
                    "publish",
                    [
                        "publish",
                        "--model",
                        c["build"]["model"],
                        "--day",
                        "{{ (dag_run.logical_date or dag_run.run_after).strftime('%Y-%m-%d') }}",
                    ],
                    c,
                    outlets=[asset(c)],
                )
                dbt >> pub
        dags[dag.dag_id] = dag
    platform = {"name": "platform", "owner": {"team": "data-platform"}}
    with DAG(
        dag_id="maintain__lake",
        schedule="30 1 * * *",
        start_date=START,
        catchup=False,
        tags=["paved-road", "maintenance"],
        doc_md="Compaction and snapshot expiry (config/platform.yaml).",
    ) as dag:
        task("maintain", ["maintain"], platform)
    dags[dag.dag_id] = dag
    return dags


globals().update(build())
