"""The simulation's shortcuts are checked against the real thing."""

import copy
import math

import pytest

from apr import conf


def same(a, b) -> bool:
    """Equal up to row order and floating-point summation order (1e-9 relative)."""
    key = [(c, "ascending") for c in a.column_names if not str(a.schema.field(c).type).startswith("double")]
    ra, rb = a.sort_by(key).to_pylist(), b.sort_by(key).to_pylist()
    if len(ra) != len(rb):
        return False
    for x, y in zip(ra, rb):
        for k in x:
            if isinstance(x[k], float) and isinstance(y[k], float):
                if not math.isclose(x[k], y[k], rel_tol=1e-9, abs_tol=1e-9):
                    return False
            elif x[k] != y[k]:
                return False
    return True


@pytest.fixture()
def workload(lake):
    from apr.workload.sim import Workload

    cfg = copy.deepcopy(conf.read("workload"))
    cfg["users"] = {"analyst": 6, "product_manager": 6, "compliance": 2, "automation": 4}
    cfg["days"] = 8
    return Workload(cfg)


def test_sim_executor_equals_the_fast_lane(workload, gateway):
    for tmpl in ["segment_week", "dashboard_trading", "customer_list", "investigation"]:
        sql = workload.sql(tmpl, 7, fixed=True, pick=1)
        user = "com000" if tmpl == "investigation" else "ana000"
        p = workload.gw.planner.plan(sql, user)
        sim = workload.fast.run(p).table
        real = gateway.fast.run(p).table
        assert same(sim, real), tmpl


def test_short_run_through_both_worlds(workload):
    from apr.workload.sim import summarise

    arr = workload.arrivals(2)
    gw, base = workload.run_gateway(arr), workload.run_baseline(arr)
    sg, sb = summarise(gw, workload), summarise(base, workload)
    assert sg["queries"] >= sb["queries"] == len(arr)
    assert sb["pii_cells_exposed"] > 0 and sg["pii_cells_exposed"] == 0
    assert not any(k.leak for k in workload.known_gw.values())
    assert sg["scanned_tb"] < sb["scanned_tb"]
    assert "partition_filter_required" in sg["outcomes"]


def test_baseline_runs_queries_as_written(workload):
    k = workload.know_baseline("SELECT * FROM raw.trades", "ana000", "analyst")
    assert k.status == "ok" and k.rows > 10000  # no row cap, no partition rule
