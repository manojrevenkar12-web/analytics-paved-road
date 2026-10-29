from apr import conf
from apr.drills.run import bad_batch, late_upstream


def _cfg():
    return dict(conf.read("drills"))


def test_a_corrupt_batch_is_quarantined_and_never_read(lake):
    d = bad_batch(_cfg())
    assert d["verdict"] == "pass", d["results"]
    assert any("positive on amount_eur" in f for f in d["results"]["failed_checks"])
    assert any("unique on trade_id" in f for f in d["results"]["failed_checks"])


def test_late_upstream_pages_the_mart_owner_and_recovers(lake):
    d = late_upstream(_cfg())
    assert d["verdict"] == "pass", d
    assert d["results"]["mart_late_minutes"] > 0
