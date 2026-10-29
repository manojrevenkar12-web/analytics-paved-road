"""The error budget policy, enforced in the deploy job: no feature rollout while a budget is spent."""

from __future__ import annotations

import json

from apr import conf


def check(status: dict | None = None) -> tuple[bool, str]:
    cfg = conf.read("slo")["budget_policy"]
    if status is None:
        p = conf.path("reports", "slo", "status.json")
        status = json.loads(p.read_text())
    lines, ok = [], True
    for o in status["objectives"]:
        left = o["budget_remaining"]
        if left < cfg["freeze_below"]:
            ok = False
            state = "FROZEN: budget spent, only reliability fixes ship"
        elif left < cfg["review_below"]:
            state = "review: reliability work comes first"
        else:
            state = "ok"
        lines.append(
            f"{o['name']}: {o['ratio']:.4%} good against {o['objective']:.1%}, {left:.0%} of the budget left ({state})"
        )
    src = "simulated" if status.get("simulated") else "measured"
    return ok, f"Error budgets over {status['window_days']} days ({src}):\n" + "\n".join(lines)
