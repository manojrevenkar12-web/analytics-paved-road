import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mockFetch, renderWithClient } from "../../test-utils";
import { AskView, SUGGESTIONS } from "./AskView";

const semantic = { metrics: [{ name: "trades", label: "Trades", description: "Executed orders.", unit: "count", dimensions: ["country"], table: "mart.daily_trading" }], dimensions: [], data_through: "2026-09-28" };

describe("ask", () => {
  it("answers with the text written from the result and shows how", async () => {
    mockFetch([
      { match: (u) => u.includes("/api/semantic"), body: semantic },
      { match: (u) => u.includes("/api/ask"), body: { question: "q", text: "Trades on 28 September 2026: 4,044.", answered: true, parser: "sim", data_through: "2026-09-28", query: { metrics: ["trades"] }, sql: ["SELECT SUM(d.trades) AS trades FROM mart.daily_trading d"], columns: ["trades"], rows: [[4044]], rule: null } },
    ]);
    renderWithClient(<AskView />);
    expect(await screen.findByText("Executed orders.")).toBeTruthy();
    fireEvent.click(screen.getByText(SUGGESTIONS[0]!));
    expect(await screen.findByText("Trades on 28 September 2026: 4,044.")).toBeTruthy();
    expect(screen.getByText("How this was answered")).toBeTruthy();
  });
  it("shows a refusal as the answer", async () => {
    mockFetch([
      { match: (u) => u.includes("/api/semantic"), body: semantic },
      { match: (u) => u.includes("/api/ask"), body: { question: "q", text: "I report what happened; I don't forecast.", answered: false, parser: "sim", data_through: "2026-09-28", query: null, sql: [], columns: [], rows: [], rule: "not_in_semantic_layer" } },
    ]);
    renderWithClient(<AskView />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Will volume grow?" } });
    fireEvent.click(screen.getByText("Ask"));
    expect(await screen.findByText(/don't forecast/)).toBeTruthy();
  });
});
