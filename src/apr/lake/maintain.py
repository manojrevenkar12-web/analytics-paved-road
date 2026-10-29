"""Table maintenance: compaction and snapshot expiry, so planning stays cheap as appends pile up.

Every daily append adds a manifest; after four months the trades table has 120 and planning a
query opens all of them. Compaction rewrites the table's current content into one snapshot (one
manifest, one file per partition) and keeps the business-day properties; expiry drops old
snapshots beyond the time-travel window. On Spark this is `rewrite_data_files` and
`rewrite_manifests`; here it runs with PyIceberg.
"""

from __future__ import annotations

from dataclasses import dataclass

from apr import conf
from apr.contracts.model import all_contracts
from apr.lake.catalog import load


@dataclass
class Maintained:
    table: str
    manifests_before: int
    manifests_after: int
    snapshots_expired: int


def maintain(tables: list[str] | None = None) -> list[Maintained]:
    cfg = conf.read("platform")["lake"]["maintenance"]
    out = []
    for name in tables or list(all_contracts()):
        t = load(name)
        snap = t.current_snapshot()
        if snap is None:
            continue
        before = len(snap.manifests(t.io))
        if before > int(cfg["max_manifests"]):
            props = {
                k: v
                for k, v in (snap.summary.additional_properties if snap.summary else {}).items()
                if k.startswith("apr.")
            }
            props["apr.maintenance"] = "compaction"
            t.overwrite(t.scan(snapshot_id=snap.snapshot_id).to_arrow(), snapshot_properties=props)
            t = t.refresh()
        snaps = sorted(t.snapshots(), key=lambda s: s.timestamp_ms)
        keep = int(cfg["keep_snapshots"])
        old = [s.snapshot_id for s in snaps[:-keep]] if len(snaps) > keep else []
        if old:
            t.maintenance.expire_snapshots().by_ids(old).commit()
            t = t.refresh()
        cur = t.current_snapshot()
        out.append(Maintained(name, before, len(cur.manifests(t.io)) if cur else 0, len(old)))
    return out
