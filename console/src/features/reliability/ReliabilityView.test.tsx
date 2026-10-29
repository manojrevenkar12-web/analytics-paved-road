import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mockFetch, renderWithClient } from "../../test-utils";
import { ReliabilityView } from "./ReliabilityView";

const obj = { name: "gateway_availability", description: "Queries that did not fail on the platform's side", objective: 0.995, good: 990, total: 1000, ratio: 0.99,
  budget_remaining: -1, alerts: [{ name: "page_fast", severity: "page", start: "2026-09-23T10:05", minutes: 40 }],
  daily: [], budget: [{ day: "a", budget_remaining: 1 }, { day: "b", budget_remaining: -1 }] };

describe("reliability", () => {
  it("shows a spent budget and the drills", async () => {
    mockFetch([
      { match: (u) => u.includes("/api/slo"), body: { window_days: 30, from: "2026-08-30", to: "2026-09-28", simulated: true, objectives: [obj] } },
      { match: (u) => u.includes("/api/drills"), body: { drills: [{ name: "spark_down", kind: "simulated", question: "If the Spark lane fails?", injected: "x", timeline: [["10:00", "down"]], results: {}, verdict: "pass" }] } },
    ]);
    renderWithClient(<ReliabilityView />);
    expect(await screen.findByText("-100%")).toBeTruthy();
    expect(screen.getByText(/simulated workload/)).toBeTruthy();
    expect(await screen.findByText("If the Spark lane fails?")).toBeTruthy();
    expect(screen.getByText("held")).toBeTruthy();
  });
  it("says when there is no report", async () => {
    mockFetch([{ match: () => true, status: 404, body: { error: "No SLO report yet; run `apr simulate`." } }]);
    renderWithClient(<ReliabilityView />);
    expect(await screen.findByText("No SLO report")).toBeTruthy();
  });
});
