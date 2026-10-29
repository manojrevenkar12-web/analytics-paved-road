import yaml

from apr import conf
from apr.slo.budget import check
from apr.slo.promtest import tests as promtests
from apr.slo.rules import budget_remaining, burn_alerts, prometheus_rules


def minutes(n: int, total: float, bad_share: float):
    return [total * (1 - bad_share)] * n, [total] * n


def test_rules_cover_every_objective_and_window():
    rules = prometheus_rules()
    names = {r.get("record") or r.get("alert") for g in rules["groups"] for r in g["rules"]}
    for obj in conf.read("slo")["objectives"]:
        for w in ("5m", "30m", "1h", "2h", "6h", "1d", "3d", "30d"):
            assert f"slo:apr_{obj}:error_ratio{w}" in names
        assert f"slo:apr_{obj}:budget_remaining" in names
    assert "APR_ProductLate" in names


def test_generated_files_are_current():
    p = conf.path("deploy", "prometheus", "rules", "slo.rules.yaml")
    on_disk = yaml.safe_load(p.read_text())
    assert on_disk == prometheus_rules(), "run `apr generate`"
    t = conf.path("deploy", "prometheus", "tests", "slo.test.yaml")
    assert yaml.safe_load(t.read_text()) == promtests(), "run `apr generate`"


def test_detection_time_follows_the_burn_rate():
    """Page when the 1h error ratio passes 14.4 x 0.5% = 7.2%: after ~5 min of a full outage,
    after ~43 min of 10% errors (60 min x 7.2 / 10)."""
    good, total = minutes(180, 100, 0.0)
    for share, lo, hi in [(1.0, 4, 6), (0.10, 42, 45)]:
        g2, t2 = minutes(90, 100, share)
        page = [a for a in burn_alerts(good + g2, total + t2, 0.995) if a.name == "page_fast"]
        assert page and lo <= page[0].start_min - 180 <= hi, (share, page)


def test_a_healthy_service_stays_quiet():
    good, total = minutes(3 * 1440, 100, 0.001)
    assert burn_alerts(good, total, 0.995) == []


def test_a_slow_burn_opens_a_ticket_not_a_page():
    good, total = minutes(4 * 1440, 100, 0.006)  # 1.2x burn
    alerts = burn_alerts(good, total, 0.995)
    assert {a.severity for a in alerts} == {"ticket"}


def test_budget_policy_freezes_deploys_when_spent():
    assert abs(budget_remaining(995, 1000, 0.995)) < 1e-9
    status = {
        "window_days": 30,
        "objectives": [{"name": "gateway_availability", "objective": 0.995, "ratio": 0.99, "budget_remaining": -1.0}],
    }
    ok, text = check(status)
    assert not ok and "FROZEN" in text
    status["objectives"][0].update(ratio=0.999, budget_remaining=0.8)
    assert check(status)[0]


def test_the_simulator_counts_every_objective():
    from apr.workload.report import slo_series

    assert set(slo_series([], 1)) == set(conf.read("slo")["objectives"])
