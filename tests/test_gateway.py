import asyncio

import pytest

from apr.gateway import audit
from apr.gateway.admission import Admission
from apr.gateway.planner import Refused

Q = "SELECT channel, COUNT(*) AS n FROM raw.trades WHERE executed_at >= DATE '2026-06-02' GROUP BY 1 ORDER BY 1"


def test_same_query_same_snapshot_is_cached(gateway):
    a = gateway.query(Q, "ana")
    b = gateway.query(Q, "ben")
    assert a.status == b.status == "ok" and not a.cached and b.cached and a.rows == b.rows


def test_masking_is_part_of_the_cache_key(gateway):
    sql = "SELECT email FROM raw.customers ORDER BY customer_id LIMIT 2"
    a = gateway.query(sql, "carl")
    b = gateway.query(sql, "ana")
    assert not b.cached and a.rows != b.rows


def test_a_new_snapshot_is_never_answered_from_an_old_result(gateway, world, lake):
    from apr.contracts.model import all_contracts
    from apr.lake.ingest import write

    sql = "SELECT COUNT(*) FROM raw.prices_daily"
    before = gateway.query(sql, "pia")
    again = gateway.query(sql, "pia")
    assert again.cached
    write(all_contracts()["raw.prices_daily"], world.prices_on(20), world.date(20))
    gateway.planner.forget()
    after = gateway.query(sql, "pia")
    assert not after.cached
    assert after.rows[0][0] == before.rows[0][0] + world.prices_on(20).num_rows
    assert after.snapshots["raw.prices_daily"] != before.snapshots["raw.prices_daily"]


def test_lanes_follow_the_estimate(gateway, monkeypatch):
    gateway.planner.cfg = dict(gateway.planner.cfg, fast_lane_max_bytes=1000)
    try:
        p = gateway.plan(Q, "ana")
        assert p.lane == "spark"
        r = gateway.query(
            "SELECT channel, COUNT(*) FROM raw.trades WHERE executed_at >= DATE '2026-06-03' GROUP BY 1", "ana"
        )
        # no Kyuubi here: degraded to the fast lane because the scan is small enough
        assert r.status == "ok" and r.degraded and r.lane == "fast"
        gateway.degrade_max = 10
        r = gateway.query(
            "SELECT channel, COUNT(*) FROM raw.trades WHERE executed_at >= DATE '2026-06-04' GROUP BY 1", "ana"
        )
        assert r.status == "error" and r.rule == "lane_unavailable" and r.http_status == 503
    finally:
        gateway.planner.cfg = dict(gateway.planner.cfg, fast_lane_max_bytes=268435456)


def test_every_request_is_audited(gateway):
    gateway.query("SELECT email FROM raw.customers", "pia")
    gateway.query("SELECT COUNT(*) FROM raw.instruments", "pia")
    last = audit.read(limit=2)
    assert [e["status"] for e in last] == ["refused", "ok"]
    assert last[0]["rule"] == "denied_column" and last[1]["snapshots"]["raw.instruments"]


def test_engine_errors_are_the_platforms(gateway):
    r = gateway.query("SELECT CAST(name AS INT) + 'x' FROM raw.instruments", "pia")
    assert r.status in ("ok", "error")
    if r.status == "error":
        assert r.rule == "engine_error" and r.http_status == 500


def test_admission_limits_per_user_and_lane():
    cfg = {"queue_timeout_s": {"fast": 0.05, "spark": 0.05}, "concurrency": {"fast": 2, "spark": 1, "per_user": 1}}

    async def scenario():
        adm = Admission(cfg)
        async with adm.slot("fast", "u1"):
            with pytest.raises(Refused) as e:
                async with adm.slot("fast", "u1"):
                    pass
            assert e.value.rule == "user_concurrency" and e.value.status == 429
            async with adm.slot("fast", "u2"):
                with pytest.raises(Refused) as e2:
                    async with adm.slot("fast", "u3"):
                        pass
                assert e2.value.rule == "queue_full"
        async with adm.slot("fast", "u1"):
            pass  # slots were released
        assert adm.waiting["fast"] == 0

    asyncio.run(scenario())
