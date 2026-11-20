/**
 * Pure helpers for the AI host Insights page (#259 step 10). The API returns
 * raw agent_daily_metrics rows (one per UTC day, missing days absent); every
 * ratio here is computed from summed numerators and denominators, and is
 * null when its denominator is 0, so the page shows a dash and charts a gap.
 */
import type { InsightsDay } from "../types/reservations";

/** What an unknown value (a zero denominator, a missing window) shows as. */
export const DASH = "—";

/** "YYYY-MM-DD" to a UTC epoch day, without parsing the string as a Date. */
function epochDay(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

function fromEpochDay(n: number): string {
  return new Date(n * 86_400_000).toISOString().slice(0, 10);
}

/** Every date from `from` to `to` inclusive (empty when from > to). */
export function datesBetween(from: string, to: string): string[] {
  const out: string[] = [];
  for (let n = epochDay(from); n <= epochDay(to); n++) out.push(fromEpochDay(n));
  return out;
}

/** The API's default range for UTC date `today`: the 30 days ending yesterday. */
export function defaultRange(today: string): { from: string; to: string } {
  const end = epochDay(today) - 1;
  return { from: fromEpochDay(end - 29), to: fromEpochDay(end) };
}

/** Whether `from`..`to` is the default range for UTC date `today`. */
export function isDefaultRange(from: string, to: string, today: string): boolean {
  const d = defaultRange(today);
  return d.from === from && d.to === to;
}

export interface DayPoint {
  day: string;
  row: InsightsDay | null;
}

/** One point per day of the range, `row` null where the API had none. */
export function fillDays(days: InsightsDay[], from: string, to: string): DayPoint[] {
  const byDay = new Map(days.map((d) => [d.day, d]));
  return datesBetween(from, to).map((day) => ({ day, row: byDay.get(day) ?? null }));
}

export function ratio(numerator: number, denominator: number): number | null {
  return denominator === 0 ? null : numerator / denominator;
}

export function formatPercent(value: number | null, lang: string): string {
  if (value === null) return DASH;
  return new Intl.NumberFormat(lang, { style: "percent", maximumFractionDigits: 1 }).format(value);
}

/** Change between two ratios in percentage points, null if either is null. */
export function pointsDelta(current: number | null, previous: number | null): number | null {
  if (current === null || previous === null) return null;
  return (current - previous) * 100;
}

export function formatPoints(points: number | null, lang: string): string {
  if (points === null) return DASH;
  return new Intl.NumberFormat(lang, { maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(points);
}

export function formatUsdAmount(value: number | null, lang: string): string {
  if (value === null) return DASH;
  return new Intl.NumberFormat(lang, { style: "currency", currency: "USD" }).format(value);
}

/** A decimal string as integer micro-dollars, so sums carry no float drift; null if it does not parse. */
function micros(amount: string): bigint | null {
  const m = /^(-?)(\d+)(?:\.(\d*))?$/.exec(amount.trim());
  if (!m) return null;
  const value = BigInt(m[2]) * 1_000_000n + BigInt((m[3] ?? "").slice(0, 6).padEnd(6, "0"));
  return m[1] ? -value : value;
}

/** Sum of amounts in micro-dollars; null if any amount is unreadable. */
function sumMicros(amounts: string[]): bigint | null {
  let total = 0n;
  for (const a of amounts) {
    const m = micros(a);
    if (m === null) return null;
    total += m;
  }
  return total;
}

function perBooking(totalMicros: bigint | null, booked: number): number | null {
  return booked === 0 || totalMicros === null ? null : Number(totalMicros) / 1_000_000 / booked;
}

export interface Kpis {
  conversations: number;
  resolution: number | null;
  conversion: number | null;
  spamBlocked: number | null;
  costPerBooking: number | null;
  billedPerBooking: number | null;
}

/** KPIs over `rows`, from summed counts. */
export function rangeKpis(rows: InsightsDay[]): Kpis {
  const sum = (f: (r: InsightsDay) => number) => rows.reduce((acc, r) => acc + f(r), 0);
  const conversations = sum((r) => r.conversations);
  const booked = sum((r) => r.booked);
  const cost = sumMicros(rows.map((r) => r.cost_usd));
  const billed = sumMicros(rows.map((r) => r.billed_usd));
  return {
    conversations,
    resolution: ratio(sum((r) => r.resolved), conversations),
    conversion: ratio(booked, sum((r) => r.booking_intent)),
    spamBlocked: ratio(sum((r) => r.blocked), sum((r) => r.screened)),
    costPerBooking: perBooking(cost, booked),
    billedPerBooking: perBooking(billed, booked),
  };
}

/**
 * The 7 days ending `to` (inclusive) and the 7 before them. A window that
 * reaches before `from` was not fetched, so it is null rather than a
 * partial sum that would read as a real comparison.
 */
export function sevenDayKpis(
  days: InsightsDay[],
  from: string,
  to: string,
): { current: Kpis | null; previous: Kpis | null } {
  const start = epochDay(from);
  const end = epochDay(to);
  const within = (lo: number, hi: number) =>
    days.filter((d) => {
      const n = epochDay(d.day);
      return n >= lo && n <= hi;
    });
  return {
    current: start <= end - 6 ? rangeKpis(within(end - 6, end)) : null,
    previous: start <= end - 13 ? rangeKpis(within(end - 13, end - 7)) : null,
  };
}

export type ChartRow = Record<string, string | number | null>;

/** Per-day chart values; covers are keyed "covers:<period>". */
export function chartRows(points: DayPoint[], periods: string[]): ChartRow[] {
  return points.map(({ day, row }) => {
    const out: ChartRow = {
      day,
      resolution: row && ratio(row.resolved, row.conversations),
      conversion: row && ratio(row.booked, row.booking_intent),
      spamBlocked: row && ratio(row.blocked, row.screened),
      llmP50: row?.llm_p50_ms ?? null,
      llmP95: row?.llm_p95_ms ?? null,
      turnP50: row?.turn_p50_ms ?? null,
      turnP95: row?.turn_p95_ms ?? null,
      costPerBooking: row && perBooking(micros(row.cost_usd), row.booked),
      billedPerBooking: row && perBooking(micros(row.billed_usd), row.booked),
    };
    for (const p of periods) out[`covers:${p}`] = row ? (row.covers_by_period[p] ?? 0) : null;
    return out;
  });
}

/** Handoff reasons summed over the range, most frequent first. */
export function handoffTotals(days: InsightsDay[]): { reason: string; count: number }[] {
  const totals = new Map<string, number>();
  for (const d of days) {
    for (const [reason, n] of Object.entries(d.handoff_reasons)) totals.set(reason, (totals.get(reason) ?? 0) + n);
  }
  return [...totals]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

/**
 * Every service period with covers in the range: those in `order` (the
 * location rules' period order) first, in that order, then the rest
 * (renamed or removed periods) alphabetically.
 */
export function coverPeriods(days: InsightsDay[], order: string[] = []): string[] {
  const seen = new Set(days.flatMap((d) => Object.keys(d.covers_by_period)));
  const ranked = order.filter((p, i) => seen.has(p) && order.indexOf(p) === i);
  const rest = [...seen].filter((p) => !ranked.includes(p)).sort();
  return [...ranked, ...rest];
}

/** Chart colours, each at least 3:1 against white: theme sage and accent darks, clear, then a red, blue and violet. */
export const PALETTE = ["#7E8C76", "#A6864A", "#1D7357", "#E0491A", "#4A6FA5", "#6B5B95"] as const;

/** A colour fixed by the period's name, so a period keeps it across ranges. */
export function periodColor(period: string): string {
  let h = 0;
  for (const ch of period) h = (Math.imul(h, 31) + ch.codePointAt(0)!) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/** The last non-null value of `key` in a chart series. */
export function latestValue(rows: ChartRow[], key: string): number | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const v = rows[i][key];
    if (typeof v === "number") return v;
  }
  return null;
}

/** The mean of the non-null values of `key` in a chart series (for day-level figures such as latency). */
export function seriesMean(rows: ChartRow[], key: string): number | null {
  const values = rows.map((r) => r[key]).filter((v): v is number => typeof v === "number");
  return values.length === 0 ? null : values.reduce((a, b) => a + b, 0) / values.length;
}
