import { queryTokens } from "../../shared/theme/tokens";
import { EmptyState } from "../../shared/ui/States";

export function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  if (!rows.length) return <EmptyState title="No rows">The query ran and matched nothing.</EmptyState>;
  const shown = rows.slice(0, queryTokens.previewRows);
  return (
    <div className="max-h-[420px] overflow-auto rounded-[var(--radius-control)] border border-line">
      <table className="data">
        <thead>
          <tr>{columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={typeof v === "number" ? "num" : ""}>
                  {v === null ? <span className="text-muted">null</span> : typeof v === "number" ? v.toLocaleString("en-GB", { maximumFractionDigits: 2 }) : String(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <p className="border-t border-line px-3 py-2 text-xs text-muted">Showing {shown.length} of {rows.length.toLocaleString()} rows.</p>
      )}
    </div>
  );
}
