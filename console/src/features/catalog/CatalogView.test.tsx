import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mockFetch, renderWithClient } from "../../test-utils";
import { CatalogView, onTime } from "./CatalogView";

const product = (over = {}) => ({
  name: "customers", table: "raw.customers", layer: "raw", domain: "customer", description: "One row per customer.",
  owner: { team: "customer-data", contact: "#customer-data" }, freshness: { schedule: "daily", expected_by: "05:00", objective: 0.99 },
  partitioning: [], require_partition_filter: false, upstream: [], consumers: [],
  columns: [{ name: "email", type: "string", class: "pii_direct", access: "denied" }, { name: "country", type: "string", class: "public", access: "clear" }],
  snapshot_id: 1, snapshots: 3, data_through: "2026-09-28", landed_at: "2026-09-29T04:30:00", rows: 10, files: 1, bytes: 2048, ...over,
});

describe("catalog", () => {
  it("shows each column's access for the signed-in role", async () => {
    mockFetch([{ match: () => true, body: [product()] }]);
    renderWithClient(<CatalogView />);
    expect(await screen.findByText("not at all")).toBeTruthy();
    expect(screen.getByText("in clear")).toBeTruthy();
    expect(screen.getByText("on time")).toBeTruthy();
  });
  it("flags a late product", () => {
    expect(onTime(product({ landed_at: "2026-09-29T07:30:00" }) as never)).toBe(false);
    expect(onTime(product({ data_through: null }) as never)).toBeNull();
  });
  it("has an empty state and an error state", async () => {
    mockFetch([{ match: () => true, body: [] }]);
    renderWithClient(<CatalogView />);
    expect(await screen.findByText("No data products yet")).toBeTruthy();
  });
});
