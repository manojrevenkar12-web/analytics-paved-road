import datetime as dt

import pytest

from apr.semantic.layer import MetricQuery, Unanswerable, compile_query, run, validate

THROUGH = dt.date(2026, 6, 8)


def q(**kw) -> MetricQuery:
    base = {"metrics": ["trades"], "start": "2026-06-01", "end": "2026-06-08"}
    return MetricQuery.model_validate(base | kw)


def test_compiles_to_the_gateway_dialect():
    sql = compile_query(
        q(
            metrics=["trading_volume_eur"],
            dimensions=["country"],
            grain="day",
            filters=[{"dimension": "asset_class", "values": ["etf"]}],
        )
    )[0].sql
    assert "FROM mart.daily_trading d" in sql
    assert "d.asset_class IN ('etf')" in sql and "DATE_TRUNC('DAY', d.trade_date)" in sql


def test_joins_only_what_a_dimension_needs():
    plain = compile_query(q(metrics=["active_traders"]))[0].sql
    by_country = compile_query(q(metrics=["active_traders"], dimensions=["country"]))[0].sql
    assert "JOIN" not in plain and "JOIN raw.customers c" in by_country and "raw.instruments" not in by_country


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"metrics": ["deposits_eur"], "dimensions": ["asset_class"]}, "cannot be broken down"),
        ({"metrics": ["deposits_eur"], "grain": "day"}, "only available by month"),
        ({"metrics": ["deposits_eur"], "start": "2026-06-03"}, "whole months"),
        ({"metrics": ["profit"]}, "not a defined metric"),
        ({"filters": [{"dimension": "channel", "values": ["fax"]}]}, "has the values"),
    ],
)
def test_questions_the_layer_cannot_answer(kw, reason):
    with pytest.raises(Unanswerable) as e:
        validate(q(**kw), THROUGH)
    assert reason in e.value.reason


def test_results_equal_hand_written_sql(gateway):
    res = run(q(metrics=["trades", "fees_eur"], dimensions=["asset_class"]), "pia", gateway, THROUGH)
    hand = gateway.query(
        "SELECT i.asset_class, COUNT(*), SUM(COALESCE(t.fee_eur, 0)) FROM raw.trades t "
        "JOIN raw.instruments i ON i.isin = t.isin WHERE t.executed_at >= DATE '2026-06-01' "
        "AND t.executed_at < DATE '2026-06-09' GROUP BY 1 ORDER BY 1",
        "ana",
    )
    assert [r[0] for r in res.rows] == [r[0] for r in hand.rows]
    for a, b in zip(res.rows, hand.rows):
        assert a[1] == b[1] and abs(a[2] - b[2]) < 1e-6


def test_metrics_from_two_sources_are_merged_on_their_keys(gateway):
    res = run(q(metrics=["trades", "active_traders"], dimensions=["country"]), "pia", gateway, THROUGH)
    assert res.refused is None and len(res.compiled) == 2
    assert res.columns == ["country", "trades", "active_traders"]
    de = next(r for r in res.rows if r[0] == "DE")
    assert de[1] > de[2] > 0


def test_access_rules_apply_to_metrics(gateway):
    res = run(q(metrics=["trading_volume_eur"]), "n8n", gateway, THROUGH)
    assert res.refused and res.rule == "denied_column"
    assert run(q(metrics=["trades"]), "n8n", gateway, THROUGH).refused is None
