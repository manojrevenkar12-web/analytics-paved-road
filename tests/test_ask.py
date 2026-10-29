import datetime as dt
import re
from pathlib import Path

import pytest
import yaml

from apr.ask import answer, parse_sim
from apr.semantic.layer import MetricQuery, Unanswerable, validate

DEV = yaml.safe_load((Path(__file__).parent / "ask" / "dev.yaml").read_text())
REF = DEV["data_through"]


def normal(q: MetricQuery) -> dict:
    return {
        "metrics": sorted(q.metrics),
        "dimensions": sorted(q.dimensions),
        "grain": q.grain,
        "start": q.start,
        "end": q.end,
        "filters": {f.dimension: sorted(f.values) for f in q.filters},
        "order": q.order,
        "limit": q.limit,
    }


def expected(e: dict) -> dict:
    return {
        "metrics": sorted(e["metrics"]),
        "dimensions": sorted(e.get("dimensions", [])),
        "grain": e.get("grain"),
        "start": e["start"],
        "end": e["end"],
        "filters": {k: sorted(v) for k, v in e.get("filters", {}).items()},
        "order": e.get("order", "natural"),
        "limit": e.get("limit"),
    }


@pytest.mark.parametrize("case", DEV["questions"], ids=[c["q"] for c in DEV["questions"]])
def test_dev_questions(case):
    got = parse_sim.parse(case["q"], REF)
    validate(got, REF)
    assert normal(got) == expected(case["expect"])


@pytest.mark.parametrize("question", DEV["refuse"])
def test_refusals(question):
    with pytest.raises(Unanswerable):
        validate(parse_sim.parse(question, REF), REF)


def test_answers_only_state_numbers_from_the_result(gateway):
    """Every figure in an answer is a result value (or the sum of the listed ones), formatted."""
    from apr.ask.service import ask

    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    checked = 0
    for question in [
        "Trades by country in the first week of June",
        "Top 3 countries by trading volume",
        "trades per day",
        "Which asset class had the most trades?",
        "average ticket on the app",
    ]:
        a = ask(question, "pia", gateway, ref=dt.date(2026, 6, 8))
        assert a.answered, (question, a.text)
        text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", a.text)
        text = re.sub(rf"\b\d{{1,2}} ({months})( \d{{4}})?", " ", text)
        text = re.sub(r"\((\d+) more in the table\)", " ", text)
        text = re.sub(r"\bfrom \d{1,2} to\b", " ", text)
        nums = [v for row in a.rows for v in row if isinstance(v, int | float)]
        metric_cols = len(a.query["metrics"])
        sums = [sum(row[-metric_cols] for row in a.rows if row[-metric_cols] is not None)] if a.rows else []
        allowed = {answer.fmt(v, u) for v in nums + sums for u in ("count", "eur")}
        for tok in re.findall(r"−?€?[\d][\d,]*(?:\.\d+)?(?:k|M|bn)?", text):
            assert tok in allowed, (question, tok, a.text)
            checked += 1
    assert checked >= 10


def test_out_of_scope_questions_name_the_reason():
    with pytest.raises(Unanswerable) as e:
        parse_sim.parse("What's the IBAN of Anna Müller?", REF)
    assert "individual customers" in e.value.reason
