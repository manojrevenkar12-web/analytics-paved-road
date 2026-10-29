import { describe, expect, it } from "vitest";

import { FAST_LANE_MAX, laneFor, position } from "./lane";

describe("lane strip axis", () => {
  it("is logarithmic and clamped", () => {
    expect(position(1024)).toBe(0);
    expect(position(1)).toBe(0);
    expect(position(1e30)).toBe(1);
    const a = position(1024 ** 2), b = position(1024 ** 3);
    expect(b - a).toBeCloseTo(position(1024 ** 3) - position(1024 ** 2));
    expect(a).toBeLessThan(b);
  });
  it("puts the 256 MiB boundary between the lanes", () => {
    expect(laneFor(FAST_LANE_MAX)).toBe("fast");
    expect(laneFor(FAST_LANE_MAX + 1)).toBe("spark");
  });
});
