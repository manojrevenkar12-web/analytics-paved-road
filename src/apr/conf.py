"""Settings live in config/*.yaml. This reads them (cached) and locates the repository."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("APR_ROOT", Path(__file__).resolve().parents[2]))


@lru_cache(maxsize=64)
def read(name: str) -> dict[str, Any]:
    with open(ROOT / "config" / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def path(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


def clear() -> None:
    read.cache_clear()
