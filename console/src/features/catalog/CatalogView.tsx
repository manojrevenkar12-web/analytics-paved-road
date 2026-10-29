import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, bytes, type Access, type Product } from "../../shared/api/client";
import { EmptyState, ErrorState, Panel, SkeletonRows } from "../../shared/ui/States";

const ACCESS_STYLE: Record<Access, string> = {
  clear: "text-clear",
  masked: "text-masked",
  denied: "text-denied line-through decoration-1",
};

export function onTime(p: Product): boolean | null {
  if (!p.data_through || !p.landed_at) return null;
  const due = new Date(`${p.data_through}T00:00:00Z`);
  due.setUTCDate(due.getUTCDate() + 1);
  const [h, m] = p.freshness.expected_by.split(":").map(Number);
  due.setUTCHours(h ?? 0, m ?? 0);
  return new Date(`${p.landed_at}Z`) <= due;
}

function ProductDetail({ p }: { p: Product }) {
  const late = onTime(p) === false;
  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-mono text-[15px] font-medium">{p.table}</h3>
        <p className="text-ink-soft">{p.description}</p>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        <div><dt className="text-muted">Owner</dt><dd>{p.owner.team} <span className="text-muted">{p.owner.contact}</span></dd></div>
        <div><dt className="text-muted">Data through</dt><dd className={late ? "text-denied" : ""}>{p.data_through ?? "never published"}</dd></div>
        <div><dt className="text-muted">Due daily by</dt><dd>{p.freshness.expected_by} UTC ({(p.freshness.objective * 100).toFixed(0)}%)</dd></div>
        <div><dt className="text-muted">Size</dt><dd>{p.rows.toLocaleString()} rows · {bytes(p.bytes)}</dd></div>
        <div><dt className="text-muted">Partitioned</dt><dd>{p.partitioning.map((x) => `${x.transform}(${x.source})`).join(", ") || "no"}{p.require_partition_filter ? ", filter required" : ""}</dd></div>
        <div><dt className="text-muted">Upstream</dt><dd>{p.upstream.join(", ") || "source system"}</dd></div>
        <div><dt className="text-muted">Snapshots kept</dt><dd>{p.snapshots}</dd></div>
      </dl>
      <table className="data">
        <thead><tr><th>Column</th><th>Type</th><th>Class</th><th>You see it</th></tr></thead>
        <tbody>
          {p.columns.map((c) => (
            <tr key={c.name}>
              <td className="font-mono text-[12px]">{c.name}</td>
              <td className="text-ink-soft">{c.type}</td>
              <td className="text-ink-soft">{c.class.replace("_", " ")}</td>
              <td className={ACCESS_STYLE[c.access]}>{c.access === "clear" ? "in clear" : c.access === "masked" ? "masked" : "not at all"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CatalogView() {
  const cat = useQuery({ queryKey: ["catalog"], queryFn: api.catalog });
  const [sel, setSel] = useState<string | null>(null);
  if (cat.isPending) return <Panel title="Data products"><SkeletonRows rows={6} label="Loading the catalog" /></Panel>;
  if (cat.isError) return <ErrorState title="Could not load the catalog" error={cat.error} onRetry={() => cat.refetch()} />;
  if (!cat.data.length) return <EmptyState title="No data products yet">Create one with <code>apr new</code>.</EmptyState>;
  const current = cat.data.find((p) => p.name === sel) ?? cat.data[0]!;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,2fr)]">
      <Panel title="Data products">
        <ul className="space-y-1">
          {cat.data.map((p) => {
            const ok = onTime(p);
            return (
              <li key={p.name}>
                <button
                  type="button"
                  aria-pressed={current.name === p.name}
                  onClick={() => setSel(p.name)}
                  className={`flex w-full items-baseline justify-between rounded-[var(--radius-control)] px-2 py-1.5 text-left ${
                    current.name === p.name ? "bg-sunk font-semibold" : "hover:bg-sunk"
                  }`}
                >
                  <span className="font-mono text-[13px]">{p.table}</span>
                  <span className={`text-xs ${ok === false ? "text-denied" : ok ? "text-clear" : "text-muted"}`}>
                    {ok === null ? "no data" : ok ? "on time" : "late"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </Panel>
      <Panel title="Contract"><ProductDetail p={current} /></Panel>
    </div>
  );
}
