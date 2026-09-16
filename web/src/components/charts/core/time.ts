import type { Time, UTCTimestamp } from "lightweight-charts";

/**
 * Convert an ISO/datetime string to a lightweight-charts Time value.
 *
 * Accepts either "T"-separated ISO ("2024-01-02T03:00:00") or space-separated
 * ("2024-01-02 03:00:00") input. Falls back to the date-only slice when the
 * input cannot be parsed, so malformed rows never break chart rendering.
 *
 * Shared by KlineChart series time conversions.
 */
export function toTime(ts: string): Time {
  const withT = ts.includes("T") ? ts : ts.replace(" ", "T");
  const date = new Date(withT);
  if (isNaN(date.getTime())) {
    return ts.slice(0, 10) as Time;
  }
  return Math.floor(date.getTime() / 1000) as UTCTimestamp;
}
