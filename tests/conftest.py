"""A small lake (8 days of the simulated brokerage, marts built by dbt) shared by the tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import warnings

import pytest

warnings.filterwarnings("ignore", message="Delete operation did not match any records")

DAYS = 8


@pytest.fixture(scope="session")
def lake():
    from apr.lake import catalog as lake_catalog
    from apr.lake.ingest import ingest
    from apr.lake.publish import dbt, publish
    from apr.world.generate import World

    d = tempfile.mkdtemp(prefix="apr-test-")
    old = {k: os.environ.get(k) for k in ("APR_WAREHOUSE", "APR_AUDIT_LOG", "APR_CATALOG")}
    os.environ["APR_WAREHOUSE"] = d
    os.environ["APR_AUDIT_LOG"] = os.path.join(d, "audit.jsonl")
    os.environ["APR_CATALOG"] = "local"
    lake_catalog.reset_cache()
    ingest(DAYS)
    assert dbt("build"), "dbt build failed"
    publish(World().date(DAYS - 1))
    yield d
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    lake_catalog.reset_cache()
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def gateway(lake):
    from apr.gateway.core import Gateway
    from apr.gateway.planner import Planner
    from apr.lake.catalog import catalog

    return Gateway(planner=Planner(catalog=catalog()))


@pytest.fixture()
def world():
    from apr.world.generate import World

    return World()
