import { useQuery } from "@tanstack/react-query";

import { api } from "../../shared/api/client";
import { EmptyState, ErrorState, Panel, SkeletonRows } from "../../shared/ui/States";

export function AuditView({ role }: { role: string | undefined }) {
  const log = useQuery({ queryKey: ["audit", role], queryFn: () => api.audit(100) });
  return (
    <Panel title="Audit log" aside={<span className="text-xs text-muted">{role === "compliance" ? "everyone's queries" : "your queries"}</span>}>
      {log.isPending ? (
        <SkeletonRows rows={6} label="Loading the audit log" />
      ) : log.isError ? (
        <ErrorState title="Could not load the audit log" error={log.error} onRetry={() => log.refetch()} />
      ) : !log.data.length ? (
        <EmptyState title="No queries yet">Everything you run, answered or refused, appears here.</EmptyState>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="data">
            <thead><tr><th>When</th><th>User</th><th>Outcome</th><th>Lane</th><th>Query</th></tr></thead>
            <tbody>
              {log.data.map((e, i) => (
                <tr key={e.query_id ?? i}>
                  <td className="font-mono text-[11px] text-muted">{e.at.slice(5, 19).replace("T", " ")}</td>
                  <td>{e.user}</td>
                  <td className={e.status === "ok" ? "text-clear" : "text-denied"}>{e.status === "ok" ? (e.cached ? "cache" : "answered") : e.rule}</td>
                  <td className="text-ink-soft">{e.lane ?? "–"}</td>
                  <td className="max-w-[520px] truncate font-mono text-[12px]" title={e.sql}>{e.sql}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
