import { useMutation, useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { api, bytes, type DryRun, type Me } from "../../shared/api/client";
import { motionTokens, queryTokens } from "../../shared/theme/tokens";
import { EmptyState, ErrorState, Panel, Skeleton } from "../../shared/ui/States";
import { LaneStrip } from "./LaneStrip";
import { RULE_TEXT } from "./lane";
import { ResultTable } from "./ResultTable";

export const EXAMPLES: { label: string; sql: string }[] = [
  {
    label: "Trades by segment, last 7 days",
    sql: "SELECT c.segment, COUNT(*) AS trades, SUM(t.amount_eur) AS volume\nFROM raw.trades t\nJOIN raw.customers c ON c.customer_id = t.customer_id\nWHERE t.executed_at >= DATE '2026-09-22'\nGROUP BY 1",
  },
  { label: "A careless full scan", sql: "SELECT * FROM raw.trades WHERE isin = 'IE0645611967'" },
  { label: "Customer e-mails", sql: "SELECT full_name, email, country FROM raw.customers LIMIT 20" },
  { label: "Daily volume from the mart", sql: "SELECT trade_date, SUM(buy_volume_eur + sell_volume_eur) AS volume\nFROM mart.daily_trading\nWHERE trade_date >= DATE '2026-09-01'\nGROUP BY 1 ORDER BY 1" },
];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Decision({ plan }: { plan: DryRun }) {
  if (plan.status === "refused") {
    return (
      <div role="status" className="rounded-[var(--radius-control)] border-l-4 border-denied bg-denied/5 p-3">
        <p className="font-semibold text-denied">{RULE_TEXT[plan.rule] ?? plan.rule}</p>
        <p className="mt-1 text-sm text-ink-soft">{plan.message}</p>
        <p className="mt-2 text-xs text-muted">Rule <code className="font-mono">{plan.rule}</code></p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div role="status" className="rounded-[var(--radius-control)] border-l-4 border-clear bg-clear/5 p-3">
        <p className="font-semibold text-clear">Runs on the {plan.lane === "fast" ? "fast lane (DuckDB)" : "Spark lane (Kyuubi)"}</p>
        <p className="mt-1 text-sm text-ink-soft">
          About {bytes(plan.est_bytes)} from storage{plan.limit_added ? `, at most ${plan.row_limit.toLocaleString()} rows back` : ""}.
        </p>
      </div>
      {plan.masked.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-ink-soft">Masked for your role</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {plan.masked.map((m) => (
              <li key={m} className="rounded-full bg-masked/10 px-2 py-0.5 font-mono text-[11px] text-masked">{m}</li>
            ))}
          </ul>
        </div>
      )}
      <table className="data">
        <thead><tr><th>Table</th><th>Files read</th><th>Estimate</th><th>Snapshot</th></tr></thead>
        <tbody>
          {plan.scans.map((s, i) => (
            <tr key={`${s.table}-${i}`}>
              <td className="font-mono text-[12px]">{s.table}</td>
              <td className="num">{s.files} of {s.files_total}</td>
              <td className="num">{bytes(s.est_bytes)}</td>
              <td className="font-mono text-[11px] text-muted">{s.snapshot_id ? String(s.snapshot_id).slice(0, 8) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QueryView({ me }: { me: Me | undefined }) {
  const [sql, setSql] = useState(EXAMPLES[0]!.sql);
  const debounced = useDebounced(sql.trim(), queryTokens.dryRunDebounceMs);
  const plan = useQuery({
    queryKey: ["dry", me?.user, debounced],
    queryFn: () => api.dryRun(debounced),
    enabled: debounced.length > 0,
  });
  const run = useMutation({ mutationFn: (s: string) => api.query(s) });
  const p = plan.data;
  const refused = p?.status === "refused";

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <Panel title="SQL" aside={<span className="text-xs text-muted">Spark SQL · checked as you type</span>}>
        <label htmlFor="sql" className="sr-only">SQL</label>
        <textarea
          id="sql"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
          className="h-52 w-full resize-y rounded-[var(--radius-control)] border border-line bg-sunk p-3 font-mono text-[13px] leading-relaxed outline-none focus:border-ink-soft"
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn btn-primary"
            disabled={!sql.trim() || run.isPending}
            onClick={() => run.mutate(sql)}
          >
            {run.isPending ? "Running…" : "Run query"}
          </button>
          {EXAMPLES.map((e) => (
            <button key={e.label} type="button" className="btn text-sm" onClick={() => setSql(e.sql)}>
              {e.label}
            </button>
          ))}
        </div>
      </Panel>

      <Panel title="Before it runs">
        {!debounced ? (
          <EmptyState title="Write a query">The gateway shows what it would read, where it would run and what you may see.</EmptyState>
        ) : plan.isPending ? (
          <div role="status" aria-label="Checking the query" className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ) : plan.isError ? (
          <ErrorState title="Could not check the query" error={plan.error} onRetry={() => plan.refetch()} />
        ) : p ? (
          <Decision plan={p} />
        ) : null}
      </Panel>

      <div className="lg:col-span-2">
        <LaneStrip
          estBytes={p ? (p.status === "ok" ? p.est_bytes : 0) : null}
          lane={p?.status === "ok" ? p.lane : null}
          refused={refused}
          limitBytes={me?.max_scan_bytes ?? null}
        />
      </div>

      <div className="lg:col-span-2">
        <Panel title="Result">
          <AnimatePresence mode="wait">
            {run.isIdle ? (
              <EmptyState key="idle" title="Nothing run yet">Run the query to see rows; repeated queries are answered from the snapshot cache.</EmptyState>
            ) : run.isPending ? (
              <div key="pending" role="status" aria-label="Running the query" className="space-y-2">
                {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-5 w-full" />)}
              </div>
            ) : run.isError ? (
              <ErrorState key="error" title="The request failed" error={run.error} onRetry={() => run.mutate(sql)} />
            ) : run.data ? (
              <motion.div key={run.data.query_id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={motionTokens.swap}>
                {run.data.status === "ok" ? (
                  <>
                    <p className="mb-2 text-sm text-ink-soft">
                      {run.data.row_count.toLocaleString()} rows · {run.data.lane} lane{run.data.degraded ? " (degraded)" : ""} ·{" "}
                      {run.data.cached ? "from cache" : `${(run.data.duration_s * 1000).toFixed(0)} ms`}
                      {run.data.truncated && <span className="ml-2 text-warn">{run.data.message}</span>}
                    </p>
                    <ResultTable columns={run.data.columns} rows={run.data.rows} />
                  </>
                ) : (
                  <ErrorState
                    title={RULE_TEXT[run.data.rule ?? ""] ?? "Refused"}
                    error={new Error(run.data.message)}
                    onRetry={run.data.rule === "queue_full" || run.data.rule === "lane_unavailable" ? () => run.mutate(sql) : undefined}
                  />
                )}
              </motion.div>
            ) : null}
          </AnimatePresence>
        </Panel>
      </div>
    </div>
  );
}
