"""The two lanes give the same answers. Runs against Kyuubi + Spark + the Iceberg REST catalog
(CI starts them on kind and sets APR_KYUUBI_HOST); skipped elsewhere."""

import datetime as dt
import os

import pytest

pytestmark = pytest.mark.spark

QUERIES = [
    (
        "ana",
        "SELECT channel, COUNT(*) AS n, SUM(amount_eur) AS v FROM raw.trades "
        "WHERE executed_at >= DATE '2026-06-02' GROUP BY 1",
    ),
    (
        "ana",
        "SELECT c.segment, COUNT(DISTINCT t.customer_id) AS traders FROM raw.trades t "
        "JOIN raw.customers c ON c.customer_id = t.customer_id WHERE t.executed_at BETWEEN "
        "TIMESTAMP '2026-06-01 00:00:00' AND TIMESTAMP '2026-06-02 23:59:59' GROUP BY 1",
    ),
    ("ana", "SELECT customer_id, email, birth_date FROM raw.customers ORDER BY customer_id LIMIT 20"),
    ("pia", "SELECT country, COUNT(*) AS n FROM raw.customers GROUP BY 1 ORDER BY 2 DESC"),
    (
        "n8n",
        "SELECT CAST(executed_at AS DATE) AS d, COUNT(*) FROM raw.trades WHERE executed_at >= "
        "DATE '2026-06-01' GROUP BY 1 ORDER BY 1",
    ),
    (
        "carl",
        "SELECT type, ROUND(SUM(amount_eur), 2) FROM raw.cash_movements WHERE booked_at >= "
        "DATE '2026-06-01' GROUP BY 1",
    ),
]


def norm(rows):
    out = []
    for r in rows:
        row = []
        for v in r:
            if isinstance(v, dt.datetime | dt.date):
                v = v.isoformat()
            if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
                v = v.replace("T", " ")[:19]
            row.append(v)
        out.append(row)
    return out


@pytest.fixture(scope="module")
def lanes():
    if not os.environ.get("APR_KYUUBI_HOST"):
        pytest.skip("no Kyuubi endpoint (APR_KYUUBI_HOST)")
    from apr.gateway.core import Gateway

    return Gateway(audit_log=False)


@pytest.mark.parametrize("user,sql", QUERIES)
def test_fast_lane_equals_spark_lane(lanes, user, sql):
    from apr.gateway.core import to_rows
    from apr.rollout.shadow import same_result

    p = lanes.plan(sql, user)
    fast = to_rows(lanes.fast.run(p).table)[1]
    spark = to_rows(lanes.spark.run(p).table)[1]
    assert len(fast) > 0
    assert same_result(norm(fast), norm(spark), rel=1e-9), (sql, fast[:3], spark[:3])
