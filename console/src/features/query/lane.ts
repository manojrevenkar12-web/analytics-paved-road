import { laneTokens } from "../../shared/theme/tokens";

/** Position (0..1) of a byte count on the strip's log axis. */
export function position(bytes: number, min: number = laneTokens.minBytes, max: number = laneTokens.maxBytes): number {
  const b = Math.min(Math.max(bytes, min), max);
  return (Math.log(b) - Math.log(min)) / (Math.log(max) - Math.log(min));
}

export const FAST_LANE_MAX = 256 * 1024 ** 2;

export function laneFor(bytes: number, fastMax: number = FAST_LANE_MAX): "fast" | "spark" {
  return bytes <= fastMax ? "fast" : "spark";
}

export const RULE_TEXT: Record<string, string> = {
  denied_column: "Your role may not read a column in this query",
  partition_filter_required: "Filter the partition column",
  scan_limit: "Over your role's scan limit",
  read_only: "Read-only queries only",
  single_statement: "One statement at a time",
  table_function: "Files and table functions are not data products",
  unknown_table: "Not a data product",
  unknown_column: "Unknown column",
  unknown_user: "Unknown user",
  parse_error: "Not valid Spark SQL",
  queue_full: "Lane at capacity, retry shortly",
  user_concurrency: "You already have 2 queries running",
  lane_unavailable: "The Spark lane is down",
  engine_error: "The engine failed",
};
