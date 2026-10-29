import { useMutation, useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";

import { api } from "../../shared/api/client";
import { motionTokens } from "../../shared/theme/tokens";
import { EmptyState, ErrorState, Panel, SkeletonRows } from "../../shared/ui/States";
import { ResultTable } from "../query/ResultTable";

export const SUGGESTIONS = [
  "Top 5 countries by trading volume last month",
  "Which asset class had the most trades last week?",
  "Net inflow by segment this quarter",
  "Daily trades for crypto in the last 14 days",
];

export function AskView() {
  const [q, setQ] = useState("");
  const ask = useMutation({ mutationFn: (question: string) => api.ask(question) });
  const sem = useQuery({ queryKey: ["semantic"], queryFn: api.semantic });
  const submit = (text: string) => {
    setQ(text);
    if (text.trim().length > 1) ask.mutate(text.trim());
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Panel title="Ask about the metrics">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(q);
            }}
            className="flex gap-2"
          >
            <label htmlFor="question" className="sr-only">Question</label>
            <input
              id="question"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="How many active traders in Germany last week?"
              className="min-w-0 flex-1 rounded-[var(--radius-control)] border border-line bg-sunk px-3 py-2 outline-none focus:border-ink-soft"
            />
            <button type="submit" className="btn btn-primary" disabled={q.trim().length < 2 || ask.isPending}>Ask</button>
          </form>
          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="btn text-sm" onClick={() => submit(s)}>{s}</button>
            ))}
          </div>
        </Panel>
        <Panel title="Answer">
          {ask.isIdle ? (
            <EmptyState title="Ask a question">Answers use the defined metrics only, run through the gateway as you, and every number comes from the result.</EmptyState>
          ) : ask.isPending ? (
            <SkeletonRows rows={3} label="Answering" />
          ) : ask.isError ? (
            <ErrorState title="Could not answer" error={ask.error} onRetry={() => ask.mutate(q)} />
          ) : ask.data ? (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={motionTokens.reveal} className="space-y-3">
              <p className={`text-lg leading-snug ${ask.data.answered ? "" : "text-denied"}`}>{ask.data.text}</p>
              <p className="text-xs text-muted">Data through {ask.data.data_through} · parser: {ask.data.parser}</p>
              {ask.data.answered && ask.data.rows.length > 1 && <ResultTable columns={ask.data.columns} rows={ask.data.rows} />}
              {ask.data.sql.length > 0 && (
                <details className="text-sm">
                  <summary className="cursor-pointer text-ink-soft">How this was answered</summary>
                  <pre className="mt-2 overflow-auto rounded-[var(--radius-control)] bg-sunk p-3 font-mono text-[12px]">
                    {JSON.stringify(ask.data.query, null, 2)}
                  </pre>
                  {ask.data.sql.map((s) => (
                    <pre key={s} className="mt-2 overflow-auto whitespace-pre-wrap rounded-[var(--radius-control)] bg-sunk p-3 font-mono text-[12px]">{s}</pre>
                  ))}
                </details>
              )}
            </motion.div>
          ) : null}
        </Panel>
      </div>
      <Panel title="Defined metrics">
        {sem.isPending ? (
          <SkeletonRows rows={6} label="Loading metrics" />
        ) : sem.isError ? (
          <ErrorState title="Could not load the metrics" error={sem.error} onRetry={() => sem.refetch()} />
        ) : sem.data.metrics.length === 0 ? (
          <EmptyState title="No metrics defined">Add one to config/metrics.yaml.</EmptyState>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {sem.data.metrics.map((m) => (
              <li key={m.name} className="py-2">
                <p className="font-semibold">{m.label}</p>
                <p className="text-ink-soft">{m.description}</p>
                <p className="mt-0.5 text-xs text-muted">by {m.dimensions.join(", ").replaceAll("_", " ")}</p>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
