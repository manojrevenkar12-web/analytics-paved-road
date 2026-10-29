import copy

from apr import conf
from apr.rollout.shadow import same_result, shadow_engine, shadow_policy


def test_policy_shadow_finds_widened_and_narrowed_access(gateway):
    for sql, user in [
        ("SELECT email FROM raw.customers LIMIT 3", "ana"),
        ("SELECT email FROM raw.customers", "pia"),
        ("SELECT COUNT(*) FROM raw.instruments", "n8n"),
    ]:
        gateway.query(sql, user)
    cand = copy.deepcopy(conf.read("roles"))
    cand["roles"]["product_manager"]["masked"] = ["pii_quasi", "pii_direct"]  # PMs would see hashed PII
    cand["roles"]["product_manager"]["denied"] = []
    cand["roles"]["analyst"]["clear"] = ["public", "internal", "financial", "pii_direct"]  # analysts unmasked
    cand["roles"]["analyst"]["masked"] = ["pii_quasi"]
    cand["users"].pop("n8n")  # n8n removed
    rep = shadow_policy(cand, catalog=gateway.planner.catalog)
    s = rep["summary"]
    assert s["newly_allowed"] >= 1 and s["unmasked"] >= 1 and s["newly_refused"] >= 1
    assert s["needs_dpo_review"] and s["breaks_users"]


def test_engine_shadow_compares_results_with_a_tolerance(gateway):
    gateway.query("SELECT asset_class, COUNT(*) FROM raw.instruments GROUP BY 1", "pia")
    from apr.gateway.core import to_rows

    same = shadow_engine(gateway, lambda p: to_rows(gateway.fast.run(p).table), limit=3)
    assert same["summary"]["different"] == 0
    off = shadow_engine(gateway, lambda p: (["x"], [["different"]]), limit=3)
    assert off["summary"]["different"] == 3
    assert same_result([[1, 0.1 + 0.2]], [[1, 0.3]])
