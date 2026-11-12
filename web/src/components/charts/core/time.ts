import type { Time, UTCTimestamp } from "lightweight-charts";

/** 时区偏移尾缀（Z/z 或 ±HH:MM/±HHMM）——命中即带偏移串 */
const OFFSET_SUFFIX = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

/**
 * ISO/datetime 串 → lightweight-charts Time 值（图表时间解析单源，三档形制域）：
 * ①带偏移串（尾缀 Z/z 或 ±HH:MM）按偏移解析（交给 Date 内建偏移语义）；
 * ②裸串（可解析且不带偏移尾缀）按 UTC 解析——datetime 形补 "Z" 后解析；
 *   date-only 形（长度 10）按 ES 规范本就是 UTC，不补（"YYYY-MM-DDZ" 非法）；
 * ③malformed 串 slice(0,10) 日期串回退——坏行不破图。
 * 属性不变式：同一时刻的裸串与带偏移串产出相同 epoch，且与运行环境时区无关。
 */
export function toTime(ts: string): Time {
  const withT = ts.includes("T") ? ts : ts.replace(" ", "T");
  const bareDatetime = withT.length > 10 && !OFFSET_SUFFIX.test(ts);
  const date = new Date(bareDatetime ? `${withT}Z` : withT);
  if (isNaN(date.getTime())) {
    return ts.slice(0, 10) as Time;
  }
  return Math.floor(date.getTime() / 1000) as UTCTimestamp;
}
