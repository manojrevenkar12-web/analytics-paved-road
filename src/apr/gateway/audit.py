"""Every request, allowed or refused, is appended to the audit log: who, as which role, what
they sent, what ran (with its snapshots), what it cost and how it ended. Shadow replays of a
platform change read this log."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def log_path() -> Path:
    from apr.lake.catalog import warehouse_dir

    return Path(os.environ.get("APR_AUDIT_LOG", f"{warehouse_dir()}/audit.jsonl"))


def append(entry: dict[str, Any]) -> None:
    entry = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"), **entry}
    line = json.dumps(entry, default=str, separators=(",", ":"))
    with _lock, open(log_path(), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read(limit: int | None = None) -> list[dict[str, Any]]:
    p = log_path()
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[-limit:] if limit else rows
