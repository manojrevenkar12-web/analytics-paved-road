import { useQuery } from "@tanstack/react-query";

import { api, type SloObjective } from "../../shared/api/client";
import { EmptyState, ErrorState, Panel, SkeletonRows } from "../../shared/ui/States";

const W = 260;
const H = 44;

/** Error budget left over the window: a falling line, the zero line in brick. */
export function BudgetLine({ o }: { o: SloObjective }) {
  const pts = o.budget.map((b, i) => [i / Math.max(o.budget.length - 1, 1), Math.max(-1, Math.min(1, b.budget_remaining))] as const);
  const y = (v: number) => H - ((v + 1) / 2) * H;
  const d = pts.map(([x, v], i) => `${i ? "L" : "M"}${(x * W).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-11 w-full" role="img" aria-label={`Budget left falls to ${(o.budget_remaining * 100).toFixed(0)}%`}>
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="var(--color-denied)" strokeDasharray="3 3" strokeWidth={1} />
      <path d={d} fill="none" stroke="var(--color-ink)" strokeWidth={1.5} />
    </svg>
  );
}

function Objective({ o }: { o: SloObjective }) {
  const left = o.budget_remaining;
  const tone = left < 0 ? "text-denied" : left < 0.25 ? "text-warn" : "text-clear";
  return (
    <li className="grid gap-3 py-3 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
      <div>
        <p className="font-semibold">{o.description}</p>
        <p className="text-sm text-ink-soft">
          {o.ratio != null ? `${(o.ratio * 100).toFixed(3)}%` : "–"} good against {(o.objective * 100).toFixed(1)}% · {o.total.toLocaleString()} events
        </p>
      </div>
      <BudgetLine o={o} />
      <div className="text-right">
        <p className={`text-2xl font-semibold ${tone}`}>{(left * 100).toFixed(0)}%</p>
        <p className="text-xs text-muted">of the error budget left</p>
        {o.alerts.length > 0 && <p className="text-xs text-warn">{o.alerts.length} alert{o.alerts.length > 1 ? "s" : ""} in the window</p>}
      </div>
    </li>
  );
}

export function ReliabilityView() {
  const slo = useQuery({ queryKey: ["slo"], queryFn: api.slo });
  const drills = useQuery({ queryKey: ["drills"], queryFn: api.drills });
  return (
    <div className="space-y-4">
      <Panel
        title="Service level objectives"
        aside={slo.data && <span className="text-xs text-muted">{slo.data.simulated ? "simulated workload" : "measured"} · {slo.data.from} to {slo.data.to}</span>}
      >
        {slo.isPending ? (
          <SkeletonRows rows={3} label="Loading objectives" />
        ) : slo.isError ? (
          <ErrorState title="No SLO report" error={slo.error} onRetry={() => slo.refetch()} />
        ) : !slo.data.objectives.length ? (
          <EmptyState title="No objectives">Add one to config/slo.yaml.</EmptyState>
        ) : (
          <ul className="divide-y divide-line">{slo.data.objectives.map((o) => <Objective key={o.name} o={o} />)}</ul>
        )}
      </Panel>
      <Panel title="Failure drills">
        {drills.isPending ? (
          <SkeletonRows rows={4} label="Loading drills" />
        ) : drills.isError ? (
          <ErrorState title="No drill report" error={drills.error} onRetry={() => drills.refetch()} />
        ) : !drills.data.drills.length ? (
          <EmptyState title="No drills run">Run <code>apr drill</code>.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {drills.data.drills.map((d) => (
              <li key={d.name}>
                <details className="rounded-[var(--radius-control)] border border-line p-3">
                  <summary className="flex cursor-pointer flex-wrap items-baseline justify-between gap-2">
                    <span><span className="font-semibold">{d.question}</span> <span className="text-xs text-muted">({d.kind})</span></span>
                    <span className={d.verdict === "pass" ? "text-clear" : "text-denied"}>{d.verdict === "pass" ? "held" : d.verdict}</span>
                  </summary>
                  <p className="mt-2 text-sm text-ink-soft">{d.injected}</p>
                  <table className="data mt-2">
                    <tbody>
                      {d.timeline.map(([t, e], i) => (
                        <tr key={i}><td className="w-16 font-mono text-[12px]">{t}</td><td className="whitespace-normal">{e}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
