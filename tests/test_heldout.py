"""The held-out sets and their first runs are pinned: neither may change (see AGENTS.md)."""

import hashlib
import json

from apr import conf

PINNED = {
    "heldout/access/cases.yaml": "b88832de1bd189a86edb5786e710b693e37112bd999add1970e8dace857bbfd2",
    "heldout/ask/questions.yaml": "14840339fcada2699b59e0a11c852f0280d0fe716549b4a7f8f369095bd8651a",
    "reports/heldout/access/first_run.json": "afde7bb942941591c0ef1caecf27b36a2f4095805e5bc93406829408dd5edb45",
    "reports/heldout/ask/first_run.json": "2edbbd60ce5c6b4fc2d9b718cf9db069345ea68aa730d4b1b696877a48e8aa0e",
}


def test_sets_and_first_runs_are_unchanged():
    for path, digest in PINNED.items():
        assert hashlib.sha256(conf.path(path).read_bytes()).hexdigest() == digest, path


def test_first_runs_name_their_set():
    for name in ("access", "ask"):
        run = json.loads(conf.path("reports", "heldout", name, "first_run.json").read_text())
        assert run["sha256"] == PINNED[run["file"]]
        assert run["commit"] == "2cc2d6d"


def test_the_sets_are_not_in_regression_status():
    for name in ("access", "ask"):
        status = conf.path("heldout", name, "STATUS")
        assert not status.exists() or status.read_text().strip() == "heldout"
