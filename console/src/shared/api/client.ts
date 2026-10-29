// Typed access to the gateway API. The identity header is the demo stand-in for the ingress's
// OIDC session; the server looks the role up itself.

export type Access = "clear" | "masked" | "denied";

export type Me = { user: string; role: string; clear: string[]; masked: string[]; denied: string[]; max_scan_bytes: number };
export type UserRow = { user: string; role: string; description: string };

export type Scan = {
  table: string; snapshot_id: number | null; columns: string[]; files: number; files_total: number;
  est_bytes: number; est_rows: number; filter: string;
};
export type DryRun =
  | { status: "ok"; lane: "fast" | "spark"; est_bytes: number; role: string; masked: string[]; row_limit: number;
      limit_added: boolean; scans: Scan[]; physical: string }
  | { status: "refused"; rule: string; message: string };

export type QueryResult = {
  query_id: string; status: "ok" | "refused" | "error"; user: string; role: string | null; rule: string | null;
  message: string; columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean;
  lane: string | null; cached: boolean; est_bytes: number; scanned_bytes: number; duration_s: number;
  snapshots: Record<string, number | null>; masked: string[]; degraded: boolean;
};

export type Product = {
  name: string; table: string; layer: "raw" | "mart"; domain: string; description: string;
  owner: { team: string; contact: string }; freshness: { schedule: string; expected_by: string; objective: number };
  partitioning: { source: string; transform: string }[]; require_partition_filter: boolean;
  upstream: string[]; consumers: string[];
  columns: { name: string; type: string; class: string; access: Access }[];
  snapshot_id: number | null; snapshots: number; data_through: string | null; landed_at: string | null;
  rows: number; files: number; bytes: number;
};

export type Semantic = {
  metrics: { name: string; label: string; description: string; unit: string; dimensions: string[]; table: string }[];
  dimensions: { name: string; label: string; values: string[] }[];
  data_through: string | null;
};

export type Answer = {
  question: string; text: string; answered: boolean; parser: string; data_through: string;
  query: Record<string, unknown> | null; sql: string[]; columns: string[]; rows: unknown[][]; rule: string | null;
};

export type SloObjective = {
  name: string; description: string; objective: number; good: number; total: number; ratio: number | null;
  budget_remaining: number;
  alerts: { name: string; severity: string; start: string; minutes: number }[];
  daily: { day: string; ratio: number | null; total: number }[];
  budget: { day: string; budget_remaining: number }[];
};
export type SloStatus = { window_days: number; from: string; to: string; simulated: boolean; objectives: SloObjective[] };

export type Drill = {
  name: string; kind: "simulated" | "real"; question: string; injected: string;
  timeline: [string, string][]; results: Record<string, unknown>; verdict: string;
};

export type AuditEntry = {
  at: string; query_id?: string; user: string; role: string | null; status: string; rule: string | null;
  sql: string; lane: string | null; cached?: boolean; est_bytes?: number; rows?: number; duration_s?: number;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let currentUser = "ana";
export function setUser(u: string) {
  currentUser = u;
}
export function getUser() {
  return currentUser;
}

async function request<T>(path: string, init?: RequestInit, acceptStatus: number[] = []): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", "x-user": currentUser, ...(init?.headers ?? {}) },
  });
  if (!res.ok && !acceptStatus.includes(res.status)) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : body.message ?? body.error ?? JSON.stringify(body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export const api = {
  me: () => request<Me>("/api/me"),
  users: () => request<UserRow[]>("/api/users"),
  dryRun: (sql: string) => request<DryRun>("/api/query", { method: "POST", body: JSON.stringify({ sql, dry_run: true }) }),
  // refusals are answers too: 400/403/429/503 carry a rule and a message
  query: (sql: string) =>
    request<QueryResult>("/api/query", { method: "POST", body: JSON.stringify({ sql }) }, [400, 403, 429, 500, 503]),
  catalog: () => request<Product[]>("/api/catalog"),
  semantic: () => request<Semantic>("/api/semantic"),
  ask: (question: string) => request<Answer>("/api/ask", { method: "POST", body: JSON.stringify({ question }) }),
  slo: () => request<SloStatus>("/api/slo"),
  drills: () => request<{ drills: Drill[] }>("/api/drills"),
  audit: (limit = 100) => request<AuditEntry[]>(`/api/audit?limit=${limit}`),
};

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
}
