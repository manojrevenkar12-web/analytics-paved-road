"""The access policy and the planner's rules, on the real (small) lake."""

import hashlib

import pytest

from apr.gateway.planner import Refused

WEEK = "executed_at >= DATE '2026-06-02'"


def refused(gateway, sql, user="ana"):
    with pytest.raises(Refused) as e:
        gateway.plan(sql, user)
    return e.value


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM raw.trades",
        "INSERT INTO raw.trades SELECT * FROM raw.trades",
        "CREATE TABLE mart.x AS SELECT 1",
        "DROP TABLE raw.customers",
        "UPDATE raw.customers SET email = 'x'",
        "MERGE INTO raw.customers t USING raw.customers s ON t.customer_id = s.customer_id WHEN MATCHED THEN DELETE",
        "CACHE TABLE raw.customers",
        "SET spark.sql.shuffle.partitions = 1",
    ],
)
def test_writes_and_session_changes_are_refused(gateway, sql):
    assert refused(gateway, sql).rule in ("read_only", "parse_error")


def test_one_statement_at_a_time(gateway):
    assert refused(gateway, "SELECT 1 FROM raw.instruments; DROP TABLE raw.trades").rule == "single_statement"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://lake/raw/customers/*.parquet')",
    ],
)
def test_files_and_table_functions_are_refused(gateway, sql):
    assert refused(gateway, sql).rule in ("table_function", "unknown_table", "parse_error")


def test_only_data_products_can_be_read(gateway):
    e = refused(gateway, "SELECT * FROM information_schema.tables")
    assert e.rule == "unknown_table"
    assert "raw.trades" in e.message


def test_unknown_users_are_refused(gateway):
    assert refused(gateway, "SELECT 1 FROM raw.instruments", user="mallory").rule == "unknown_user"


def test_denied_columns_are_refused_with_the_owner(gateway):
    e = refused(gateway, "SELECT email FROM raw.customers", user="pia")
    assert e.rule == "denied_column" and "#customer-data" in e.message and e.status == 403
    e = refused(gateway, f"SELECT SUM(amount_eur) FROM raw.trades WHERE {WEEK}", user="n8n")
    assert e.rule == "denied_column" and "financial" in e.message


def test_denied_columns_cannot_be_reached_through_a_subquery_or_cte(gateway):
    assert refused(gateway, "SELECT x FROM (SELECT iban AS x FROM raw.customers) s", user="pia").rule == "denied_column"
    assert refused(gateway, "WITH c AS (SELECT * FROM raw.customers) SELECT c.email FROM c", user="pia").rule in (
        "denied_column",
        "unknown_column",
    )


def test_star_expands_to_what_the_role_may_see(gateway):
    r = gateway.query("SELECT * FROM raw.customers LIMIT 3", "pia")
    assert r.status == "ok"
    assert {"full_name", "email", "iban", "birth_date"}.isdisjoint(r.columns)
    assert "country" in r.columns


def test_masked_columns_are_hashed_or_generalised(gateway):
    clear = gateway.query(
        "SELECT customer_id, email, birth_date FROM raw.customers ORDER BY customer_id LIMIT 5", "carl"
    )
    masked = gateway.query(
        "SELECT customer_id, email, birth_date FROM raw.customers ORDER BY customer_id LIMIT 5", "ana"
    )
    for c, m in zip(clear.rows, masked.rows):
        assert c[0] == m[0]
        assert m[1] == hashlib.sha256(c[1].encode()).hexdigest()
        assert m[2] == c[2][:4] + "-01-01"
    assert masked.masked == ["raw.customers.birth_date", "raw.customers.email"]


def test_masked_values_still_join_and_count(gateway):
    a = gateway.query("SELECT COUNT(DISTINCT email) FROM raw.customers", "ana").rows[0][0]
    c = gateway.query("SELECT COUNT(DISTINCT email) FROM raw.customers", "carl").rows[0][0]
    assert a == c


def test_a_masked_column_cannot_be_probed_with_a_filter(gateway):
    email = gateway.query("SELECT email FROM raw.customers ORDER BY customer_id LIMIT 1", "carl").rows[0][0]
    sql = f"SELECT COUNT(*) FROM raw.customers WHERE email = '{email}'"
    assert gateway.query(sql, "carl").rows[0][0] == 1
    assert gateway.query(sql, "ana").rows[0][0] == 0
    p = gateway.plan(sql, "ana")
    assert "email" not in str(p.scans[0].row_filter)


def test_big_tables_need_a_partition_filter(gateway):
    e = refused(gateway, "SELECT COUNT(*) FROM raw.trades")
    assert e.rule == "partition_filter_required" and "executed_at" in e.message
    e = refused(gateway, "SELECT COUNT(*) FROM raw.trades WHERE isin = 'DE0000000000'")
    assert e.rule == "partition_filter_required"


@pytest.mark.parametrize(
    "where",
    [
        "executed_at >= DATE '2026-06-05'",
        "executed_at BETWEEN TIMESTAMP '2026-06-03 00:00:00' AND TIMESTAMP '2026-06-03 23:59:59'",
        "DATE '2026-06-05' <= executed_at",
        "executed_at >= current_date() - INTERVAL 400 DAYS",
        "CAST(executed_at AS DATE) IN (DATE '2026-06-02', DATE '2026-06-03')",
    ],
)
def test_partition_filters_in_their_usual_forms(gateway, where):
    p = gateway.plan(f"SELECT COUNT(*) FROM raw.trades WHERE {where}", "ana")
    assert p.scans[0].files <= p.scans[0].files_total


def test_partition_filter_on_a_join_side(gateway):
    p = gateway.plan(
        "SELECT c.country, COUNT(*) FROM raw.customers c JOIN raw.trades t "
        "ON t.customer_id = c.customer_id AND t.executed_at >= DATE '2026-06-07' GROUP BY 1",
        "ana",
    )
    trades = next(s for s in p.scans if s.table == "raw.trades")
    assert trades.files < trades.files_total


def test_pruning_lowers_the_estimate(gateway):
    wide = gateway.plan("SELECT COUNT(*) FROM raw.trades WHERE executed_at >= DATE '2026-06-01'", "ana")
    narrow = gateway.plan("SELECT COUNT(*) FROM raw.trades WHERE executed_at >= DATE '2026-06-07'", "ana")
    assert narrow.est_bytes < wide.est_bytes
    assert narrow.scans[0].files == 2 and wide.scans[0].files == 8
    more_cols = gateway.plan("SELECT * FROM raw.trades WHERE executed_at >= DATE '2026-06-07'", "ana")
    assert more_cols.est_bytes > narrow.est_bytes


def test_role_scan_limits(gateway, monkeypatch):
    gateway.planner.policy.cfg["roles"]["automation"]["max_scan_bytes"] = 10
    try:
        e = refused(gateway, f"SELECT channel, COUNT(*) FROM raw.trades WHERE {WEEK} GROUP BY 1", user="n8n")
        assert e.rule == "scan_limit"
    finally:
        gateway.planner.policy.cfg["roles"]["automation"]["max_scan_bytes"] = 1073741824


def test_rows_are_capped_and_said_so(gateway):
    r = gateway.query(f"SELECT trade_id FROM raw.trades WHERE {WEEK}", "ana")
    assert r.row_count == 10000 and r.truncated and "first 10,000" in r.message
    r = gateway.query(f"SELECT trade_id FROM raw.trades WHERE {WEEK} LIMIT 7", "ana")
    assert r.row_count == 7 and not r.truncated


def test_names_resolve_with_quotes_case_and_catalog(gateway):
    for sql in [
        "SELECT COUNT(*) FROM `raw`.`instruments`",
        "select count(*) from RAW.INSTRUMENTS",
        "SELECT COUNT(*) FROM lake.raw.instruments",
        "SELECT COUNT(*) FROM raw.instruments -- DROP TABLE x",
    ]:
        r = gateway.query(sql, "pia")
        assert r.status == "ok", (sql, r.message)
        assert r.rows[0][0] == 1500


def test_unions_and_ctes(gateway):
    r = gateway.query(
        "WITH a AS (SELECT asset_class FROM raw.instruments) SELECT asset_class, COUNT(*) FROM a "
        "GROUP BY 1 UNION ALL SELECT 'all', COUNT(*) FROM raw.instruments",
        "pia",
    )
    assert r.status == "ok" and dict(r.rows)["all"] == 1500


def test_physical_sql_pins_snapshots_on_both_lanes(gateway):
    p = gateway.plan(f"SELECT COUNT(*) FROM raw.trades WHERE {WEEK}", "ana")
    sid = p.scans[0].snapshot_id
    assert f"VERSION AS OF {sid}" in p.physical["spark"]
    assert "lake.raw.trades" in p.physical["spark"]
