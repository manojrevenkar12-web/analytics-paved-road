import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(gateway):
    from apr.api.app import create_app

    return TestClient(create_app(gateway))


def h(user):
    return {"X-User": user}


def test_identity_is_required(client):
    assert client.post("/api/query", json={"sql": "SELECT 1 FROM raw.instruments"}).status_code == 401
    assert client.post("/api/query", json={"sql": "SELECT 1"}, headers=h("eve")).status_code == 403


def test_query_and_refusal(client):
    r = client.post(
        "/api/query", json={"sql": "SELECT asset_class, COUNT(*) FROM raw.instruments GROUP BY 1"}, headers=h("pia")
    )
    assert r.status_code == 200 and r.json()["row_count"] == 4
    r = client.post("/api/query", json={"sql": "SELECT iban FROM raw.customers"}, headers=h("pia"))
    assert r.status_code == 403 and r.json()["rule"] == "denied_column"
    r = client.post("/api/query", json={"sql": "SELECT COUNT(*) FROM raw.trades"}, headers=h("ana"))
    assert r.status_code == 400 and r.json()["rule"] == "partition_filter_required"


def test_dry_run_previews_the_cost(client):
    r = client.post(
        "/api/query",
        json={"sql": "SELECT * FROM raw.trades WHERE executed_at >= DATE '2026-06-07'", "dry_run": True},
        headers=h("ana"),
    ).json()
    assert r["status"] == "ok" and r["lane"] == "fast" and r["est_bytes"] > 0
    assert r["scans"][0]["files"] == 2 and r["scans"][0]["files_total"] == 8
    assert "LIMIT 10000" in r["physical"]


def test_catalog_shows_each_role_its_access(client):
    pia = {p["name"]: p for p in client.get("/api/catalog", headers=h("pia")).json()}
    ana = {p["name"]: p for p in client.get("/api/catalog", headers=h("ana")).json()}
    email = lambda cat: next(c for c in cat["customers"]["columns"] if c["name"] == "email")["access"]  # noqa: E731
    assert email(pia) == "denied" and email(ana) == "masked"
    assert pia["trades"]["data_through"] == "2026-06-08" and pia["trades"]["rows"] > 0


def test_ask_and_semantic_catalog(client):
    sem = client.get("/api/semantic").json()
    assert {m["name"] for m in sem["metrics"]} >= {"trades", "net_inflow_eur"} and sem["data_through"] == "2026-06-08"
    a = client.post("/api/ask", json={"question": "trades by asset class"}, headers=h("pia")).json()
    assert a["answered"] and a["columns"] == ["asset_class", "trades"] and a["sql"]


def test_audit_is_private_except_to_compliance(client):
    client.post("/api/query", json={"sql": "SELECT COUNT(*) FROM raw.instruments"}, headers=h("ben"))
    assert {e["user"] for e in client.get("/api/audit", headers=h("ben")).json()} == {"ben"}
    users = {e["user"] for e in client.get("/api/audit?limit=500", headers=h("carl")).json()}
    assert "ben" in users and len(users) > 1


def test_metrics_endpoint_exports_freshness(client):
    client.post("/api/query", json={"sql": "SELECT COUNT(*) FROM raw.instruments"}, headers=h("ben"))
    text = client.get("/metrics").text
    assert 'apr_product_data_through_timestamp_seconds{product="trades"}' in text
    assert "apr_gateway_requests_total" in text and "apr_product_freshness_on_time" in text
