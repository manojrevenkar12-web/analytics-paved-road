import copy

import pytest
import yaml

from apr.contracts.generate import dbt_schema, grafana_dashboard
from apr.contracts.lint import breaking, lint, lint_contract
from apr.contracts.model import Contract, all_contracts, by_name


def test_all_contracts_load_and_lint_clean():
    assert len(all_contracts()) == 8
    errors = [f for f in lint() if f.level == "error"]
    assert errors == []


def _variant(name: str, **changes) -> Contract:
    d = copy.deepcopy(by_name(name).model_dump(by_alias=True))
    d.update(changes)
    return Contract.model_validate(d)


def test_personal_data_must_be_classified_as_such():
    c = by_name("customers").model_dump(by_alias=True)
    c["columns"] = [dict(col, **{"class": "internal"}) if col["name"] == "email" else col for col in c["columns"]]
    f = lint_contract(Contract.model_validate(c), all_contracts())
    assert any(x.rule == "pii_classification" and "email" in x.message for x in f)


def test_a_mart_cannot_be_due_before_its_upstream():
    c = _variant("daily_trading", freshness={"schedule": "daily", "expected_by": "05:30", "objective": 0.99})
    f = lint_contract(c, all_contracts())
    assert any(x.rule == "freshness_order" for x in f)


def test_breaking_changes_are_errors():
    old = by_name("trades")
    d = old.model_dump(by_alias=True)
    d["columns"] = [c for c in d["columns"] if c["name"] != "venue"]
    d["columns"] = [dict(c, type="string") if c["name"] == "quantity" else c for c in d["columns"]]
    rules = {f.rule for f in breaking(old, Contract.model_validate(d))}
    assert {"breaking_removed", "breaking_type"} <= rules
    cust = by_name("customers")
    d = cust.model_dump(by_alias=True)
    d["columns"] = [dict(c, **{"class": "internal"}) if c["name"] == "iban" else c for c in d["columns"]]
    assert any(f.rule == "classification_lowered" for f in breaking(cust, Contract.model_validate(d)))


def test_invalid_contracts_are_rejected():
    d = by_name("trades").model_dump(by_alias=True)
    d["partitioning"] = [{"source": "nope", "transform": "day"}]
    with pytest.raises(ValueError):
        Contract.model_validate(d)
    d = by_name("trades").model_dump(by_alias=True)
    d["columns"].append(d["columns"][0])
    with pytest.raises(ValueError):
        Contract.model_validate(d)


def test_dbt_schema_is_generated_from_the_contracts():
    s = dbt_schema()
    names = {m["name"] for m in s["models"]}
    assert names == {"daily_trading", "customer_month"}
    dt = next(m for m in s["models"] if m["name"] == "daily_trading")
    assert dt["config"]["contract"]["enforced"] is True
    assert [c["name"] for c in dt["columns"]] == [c.name for c in by_name("daily_trading").columns]
    from apr import conf

    on_disk = yaml.safe_load(conf.path("transform", "models", "marts", "_contracts.yml").read_text())
    assert on_disk == s, "run `apr generate`"


def test_dashboard_has_a_freshness_panel_per_product():
    d = grafana_dashboard()
    titles = [p["title"] for p in d["panels"]]
    for c in all_contracts().values():
        assert f"{c.name}: data age" in titles
