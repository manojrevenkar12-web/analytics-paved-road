import datetime as dt

import pyarrow.compute as pc

from apr.contracts.model import all_contracts
from apr.world.generate import World, _alnum_to_digits


def test_deterministic(world):
    a, b = world.trades_on(3), World().trades_on(3)
    assert a.equals(b)


def test_batches_match_their_contracts(world):
    for table, batch in world.day(2).items():
        assert batch.schema.equals(all_contracts()[table].arrow_schema()), table


def test_ibans_and_isins_carry_valid_check_digits(world):
    for iban in world.customers.table.column("iban").to_pylist()[:500]:
        rearranged = iban[4:] + iban[:4]
        assert int(_alnum_to_digits(rearranged)) % 97 == 1, iban
    for isin in world.instruments.column("isin").to_pylist()[:300]:
        digits = _alnum_to_digits(isin[:-1])
        total = 0
        for i, ch in enumerate(reversed(digits)):
            n = int(ch) * (2 if i % 2 == 0 else 1)
            total += n - 9 if n > 9 else n
        assert (10 - total % 10) % 10 == int(isin[-1]), isin


def test_savings_plans_run_on_execution_days_without_fees(world):
    days = world.cfg["savings_plans"]["execution_days"]
    for i in range(10):
        t = world.trades_on(i)
        sp = t.filter(pc.equal(t.column("channel"), "savings_plan"))
        d = world.date(i)
        if sp.num_rows and d.weekday() != 0:
            assert d.day in days, d
        assert pc.sum(sp.column("fee_eur")).as_py() in (0, None)


def test_weekends_trade_crypto_only(world):
    sat = next(i for i in range(7) if world.date(i).weekday() == 5)
    t = world.trades_on(sat)
    inst = dict(zip(world.instruments.column("isin").to_pylist(), world.instruments.column("asset_class").to_pylist()))
    manual = [
        inst[x] for x, ch in zip(t.column("isin").to_pylist(), t.column("channel").to_pylist()) if ch != "savings_plan"
    ]
    assert manual and set(manual) == {"crypto"}


def test_cash_signs_and_monthly_bookings(world):
    first = next(i for i in range(40) if world.date(i).day == 1)
    c = world.cash_on(first)
    types = set(c.column("type").to_pylist())
    assert {"interest", "saveback"} <= types
    for kind, sign in [("deposit", 1), ("withdrawal", -1), ("card_spend", -1), ("saveback", 1)]:
        amt = c.filter(pc.equal(c.column("type"), kind)).column("amount_eur")
        assert all(v * sign >= 0 for v in amt.to_pylist()), kind
    sb = c.filter(pc.equal(c.column("type"), "saveback")).column("amount_eur")
    assert pc.max(sb).as_py() <= world.cfg["cash"]["saveback_cap_eur"]
    assert world.date(first) >= dt.date(2026, 6, 1)
