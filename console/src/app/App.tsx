import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { AskView } from "../features/ask/AskView";
import { AuditView } from "../features/audit/AuditView";
import { CatalogView } from "../features/catalog/CatalogView";
import { QueryView } from "../features/query/QueryView";
import { ReliabilityView } from "../features/reliability/ReliabilityView";
import { api, getUser, setUser } from "../shared/api/client";
import { motionTokens, STORAGE_USER_KEY } from "../shared/theme/tokens";

const TABS = [
  { id: "query", label: "Query" },
  { id: "ask", label: "Ask" },
  { id: "catalog", label: "Catalog" },
  { id: "reliability", label: "Reliability" },
  { id: "audit", label: "Audit" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function initialUser(): string {
  try {
    return localStorage.getItem(STORAGE_USER_KEY) ?? "ana";
  } catch {
    return "ana";
  }
}

export function App() {
  const [tab, setTab] = useState<Tab>("query");
  const [user, setU] = useState(() => {
    const u = initialUser();
    setUser(u);
    return u;
  });
  const client = useQueryClient();
  const me = useQuery({ queryKey: ["me", user], queryFn: api.me });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const sem = useQuery({ queryKey: ["semantic"], queryFn: api.semantic });

  useEffect(() => {
    setUser(user);
    try {
      localStorage.setItem(STORAGE_USER_KEY, user);
    } catch {
      /* storage unavailable */
    }
    client.invalidateQueries();
  }, [user, client]);

  return (
    <div className="mx-auto max-w-[1280px] px-4 pb-10 sm:px-6">
      <motion.header
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={motionTokens.reveal}
        className="flex flex-wrap items-center justify-between gap-3 py-4"
      >
        <div className="flex items-center gap-3">
          <span aria-hidden="true" className="flex h-7 w-7 flex-col justify-center gap-1 rounded-[var(--radius-control)] bg-asphalt px-1.5">
            <span className="h-0.5 w-full bg-marking" />
            <span className="h-0.5 w-1/2 bg-marking" />
          </span>
          <h1 className="text-[19px] font-bold tracking-tight">Paved road</h1>
        </div>
        <nav aria-label="Sections" className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-current={tab === t.id ? "page" : undefined}
              onClick={() => setTab(t.id)}
              className={`relative px-3 py-1.5 text-[15px] ${tab === t.id ? "font-semibold" : "text-ink-soft hover:text-ink"}`}
            >
              {t.label}
              {tab === t.id && <motion.span layoutId="tab-mark" className="absolute inset-x-2 -bottom-0.5 h-[3px] rounded bg-marking" />}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-3 text-sm">
          {sem.data?.data_through && <span className="text-ink-soft">Data through {sem.data.data_through}</span>}
          <label className="flex items-center gap-1.5">
            <span className="text-muted">Signed in as</span>
            <select
              value={user}
              onChange={(e) => setU(e.target.value)}
              className="rounded-[var(--radius-control)] border border-line bg-panel px-2 py-1"
            >
              {(users.data ?? [{ user: getUser(), role: me.data?.role ?? "", description: "" }]).map((u) => (
                <option key={u.user} value={u.user}>{u.user} ({u.role.replace("_", " ")})</option>
              ))}
            </select>
          </label>
        </div>
      </motion.header>
      <AnimatePresence mode="wait">
        <motion.main key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={motionTokens.reveal}>
          {tab === "query" && <QueryView me={me.data} />}
          {tab === "ask" && <AskView />}
          {tab === "catalog" && <CatalogView />}
          {tab === "reliability" && <ReliabilityView />}
          {tab === "audit" && <AuditView role={me.data?.role} />}
        </motion.main>
      </AnimatePresence>
    </div>
  );
}
