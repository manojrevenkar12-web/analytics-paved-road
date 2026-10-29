import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mockFetch, renderWithClient } from "../../test-utils";
import { QueryView } from "./QueryView";

const me = { user: "pia", role: "product_manager", clear: [], masked: [], denied: [], max_scan_bytes: 5 * 1024 ** 3 };
const okPlan = {
  status: "ok", lane: "fast", est_bytes: 3 * 1024 ** 2, role: "analyst", masked: ["raw.customers.email"],
  row_limit: 10000, limit_added: true, physical: "SELECT 1",
  scans: [{ table: "raw.trades", snapshot_id: 123456789, columns: ["x"], files: 7, files_total: 120, est_bytes: 3 * 1024 ** 2, est_rows: 10, filter: "" }],
};

describe("query view", () => {
  it("shows the decision, the scan and the lane before running", async () => {
    mockFetch([{ match: (_u, i) => String(i?.body).includes('"dry_run":true'), body: okPlan }]);
    renderWithClient(<QueryView me={me} />);
    expect(await screen.findByText(/Runs on the fast lane/)).toBeTruthy();
    expect(screen.getByText("7 of 120")).toBeTruthy();
    expect(screen.getByText("raw.customers.email")).toBeTruthy();
    expect(screen.getByLabelText(/Lane strip: 3.00 MiB in the fast lane/)).toBeTruthy();
  });

  it("explains a refusal with its rule", async () => {
    mockFetch([{ match: () => true, body: { status: "refused", rule: "denied_column", message: "pia (product_manager) may not read raw.customers.email" } }]);
    renderWithClient(<QueryView me={me} />);
    expect(await screen.findByText(/may not read raw.customers.email/)).toBeTruthy();
    expect(screen.getByLabelText(/refused before the road/)).toBeTruthy();
  });

  it("shows a retry when checking fails", async () => {
    mockFetch([{ match: () => true, status: 502, body: { detail: "gateway unreachable" } }]);
    renderWithClient(<QueryView me={me} />);
    expect(await screen.findByText("Could not check the query")).toBeTruthy();
    expect(screen.getByText("Try again")).toBeTruthy();
  });

  it("runs and shows rows, or the refusal as an answer", async () => {
    mockFetch([
      { match: (_u, i) => String(i?.body).includes('"dry_run":true'), body: okPlan },
      {
        match: () => true,
        body: { query_id: "q1", status: "ok", columns: ["segment", "trades"], rows: [["saver", 12], ["trader", 30]], row_count: 2,
                truncated: false, lane: "fast", cached: false, duration_s: 0.04, message: "", degraded: false },
      },
    ]);
    renderWithClient(<QueryView me={me} />);
    expect(screen.getByText("Nothing run yet")).toBeTruthy();
    fireEvent.click(screen.getByText("Run query"));
    await waitFor(() => expect(screen.getByText("saver")).toBeTruthy());
    expect(screen.getByText(/2 rows · fast lane · 40 ms/)).toBeTruthy();
  });
});
