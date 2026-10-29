import pyarrow as pa

from apr.contracts.model import all_contracts
from apr.lake.catalog import load
from apr.lake.ingest import audit, write


def test_published_snapshots_carry_the_business_day(lake):
    snap = load("raw.trades").current_snapshot()
    assert snap.summary["apr.day"] == "2026-06-08"
    assert "apr.landed_at" in snap.summary.additional_properties
    assert list(load("raw.trades").refs()) == ["main"], "audit branches are removed after publishing"


def test_a_failing_batch_never_reaches_main(lake, world):
    c = all_contracts()["raw.trades"]
    t = load(c.table)
    before = t.current_snapshot().snapshot_id
    batch = world.trades_on(2)
    bad = batch.set_column(batch.schema.get_field_index("side"), "side", pa.array(["hold"] * batch.num_rows))
    run = write(c, bad, world.date(2))
    assert run.status == "quarantined"
    assert any(x.check == "accepted_values" and not x.ok for x in run.checks)
    after = load(c.table)
    assert after.current_snapshot().snapshot_id == before
    assert list(after.refs()) == ["main"]


def test_audit_checks():
    c = all_contracts()["raw.cash_movements"]
    batch = pa.table(
        {
            "movement_id": ["a", "a"],
            "customer_id": ["c", None],
            "booked_at": pa.array([0, 0], pa.timestamp("us")),
            "type": ["deposit", "gift"],
            "amount_eur": [1.0, 2.0],
        },
        schema=c.arrow_schema(),
    )
    res = {(r.check, r.column): r.ok for r in audit(c, batch, 50)}
    assert res[("unique", "movement_id")] is False
    assert res[("not_null", "customer_id")] is False
    assert res[("accepted_values", "type")] is False
    assert res[("row_count_min", None)] is False
