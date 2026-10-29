"""Claude as the question parser (APR_ASK=claude). It sees the metric catalog and the question,
never any data, and must answer with a MetricQuery or a refusal; the result is validated like
any other MetricQuery and the answer is still written by code."""

from __future__ import annotations

import datetime as dt
import json
import os

from apr import conf
from apr.semantic.layer import MetricQuery, Unanswerable, catalog


def _tool() -> dict:
    schema = MetricQuery.model_json_schema()
    return {
        "name": "answer_with",
        "description": "Map the question onto the metric catalog, or refuse.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": schema,
                "refuse": {"type": "string", "description": "Why the catalog cannot answer it"},
            },
            "$defs": schema.pop("$defs", {}),
        },
    }


def system_prompt(ref: dt.date) -> str:
    cat = catalog()
    lines = [
        f"Data is complete through {ref.isoformat()}; relative periods ('last month') are relative to that day.",
        "Use only these metrics and dimensions. Weeks start on Monday. 'Last month' is the last complete "
        "calendar month. Without a period, use the last 30 days (whole months for monthly metrics).",
        "Refuse questions about individual customers, forecasts or causes, and anything the catalog lacks.",
        "",
        "Metrics:",
    ]
    for name, m in cat["metrics"].items():
        dims = ", ".join(cat["sources"][m["source"]]["dimensions"])
        floor = cat["sources"][m["source"]].get("grain_floor")
        lines.append(f"- {name}: {m['description']} Dimensions: {dims}." + (f" Only by {floor}." if floor else ""))
    lines.append("\nDimension values:")
    for name, d in cat["dimensions"].items():
        lines.append(f"- {name}: {', '.join(d.get('values', [])) or 'ISO country codes' if name == 'country' else ''}")
    return "\n".join(lines)


def parse(question: str, ref: dt.date) -> MetricQuery:
    import anthropic  # optional dependency: pip install .[ai]

    cfg = conf.read("ask")["claude"]
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(  # type: ignore[call-overload]
        model=cfg["model"],
        max_tokens=1024,
        system=system_prompt(ref),
        tools=[_tool()],
        tool_choice={"type": "tool", "name": "answer_with"},
        messages=[{"role": "user", "content": question}],
    )
    block = next(b for b in msg.content if b.type == "tool_use")
    data = block.input if isinstance(block.input, dict) else json.loads(block.input)
    if data.get("refuse") or not data.get("query"):
        raise Unanswerable(data.get("refuse") or "The catalog cannot answer that.")
    return MetricQuery.model_validate(data["query"])
