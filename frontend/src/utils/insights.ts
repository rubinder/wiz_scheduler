/**
 * Pure helpers for the AI host Insights page (#259 step 10). The API returns
 * raw agent_daily_metrics rows (one per UTC day, missing days absent); every
 * ratio here is computed from summed numerators and denominators, and is
 * null when its denominator is 0, so the page shows a dash and charts a gap.
 */
import type { InsightsDay } from "../types/reservations";

const DASH = "—";

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

/** A decimal string as integer micro-dollars, so sums carry no float drift. */
function micros(amount: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d*))?$/.exec(amount.trim());
  if (!m) return 0n;
  const value = BigInt(m[2]) * 1_000_000n + BigInt((m[3] ?? "").slice(0, 6).padEnd(6, "0"));
  return m[1] ? -value : value;
}

function perBooking(totalMicros: bigint, booked: number): number | null {
  return booked === 0 ? null : Number(totalMicros) / 1_000_000 / booked;
}

export interface Kpis {
  conversations: number;
  resolution: number | null;
  conversion: number | null;
  spamBlocked: number | null;
  costPerBooking: number | null;
  billedPerBooking: number | null;
}

function kpisOf(rows: InsightsDay[]): Kpis {
  const sum = (f: (r: InsightsDay) => number) => rows.reduce((acc, r) => acc + f(r), 0);
  const conversations = sum((r) => r.conversations);
  const booked = sum((r) => r.booked);
  const cost = rows.reduce((acc, r) => acc + micros(r.cost_usd), 0n);
  const billed = rows.reduce((acc, r) => acc + micros(r.billed_usd), 0n);
  return {
    conversations,
    resolution: ratio(sum((r) => r.resolved), conversations),
    conversion: ratio(booked, sum((r) => r.booking_intent)),
    spamBlocked: ratio(sum((r) => r.blocked), sum((r) => r.screened)),
    costPerBooking: perBooking(cost, booked),
    billedPerBooking: perBooking(billed, booked),
  };
}

/** The 7 days ending `to` (inclusive) and the 7 before them. */
export function sevenDayKpis(days: InsightsDay[], to: string): { current: Kpis; previous: Kpis } {
  const end = epochDay(to);
  const within = (lo: number, hi: number) =>
    days.filter((d) => {
      const n = epochDay(d.day);
      return n >= lo && n <= hi;
    });
  return { current: kpisOf(within(end - 6, end)), previous: kpisOf(within(end - 13, end - 7)) };
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

/** Every service period with covers in the range, sorted. */
export function coverPeriods(days: InsightsDay[]): string[] {
  return [...new Set(days.flatMap((d) => Object.keys(d.covers_by_period)))].sort();
}
