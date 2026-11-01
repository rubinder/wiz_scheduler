// Date helpers for availability entry. Availability is the location's local
// wall-clock time (#171), so dates stay strings: `new Date("YYYY-MM-DD")`
// parses as UTC midnight and reads back as the previous day in any browser
// west of UTC.

import type { EmployeeAvailability } from "../types";

export type QuickSetKind = "weekdays" | "weekend" | "week";

// Day offsets from the week's Monday (0 = Mon … 6 = Sun)
export const QUICK_SET_OFFSETS: Record<QuickSetKind, number[]> = {
  weekdays: [0, 1, 2, 3, 4],
  weekend: [5, 6],
  week: [0, 1, 2, 3, 4, 5, 6],
};

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Split a `YYYY-MM-DD` string into its parts without a timezone conversion. */
export function dateParts(date: string): { year: number; month: number; day: number } {
  const [year, month, day] = date.split("-").map(Number);
  return { year, month, day };
}

/** The dates of `kind` in the Monday-to-Sunday week containing `anchor`. */
export function quickSetDates(anchor: string, kind: QuickSetKind): string[] {
  const { year, month, day } = dateParts(anchor);
  // UTC arithmetic on a calendar date: no DST or browser-zone drift.
  const base = new Date(Date.UTC(year, month - 1, day));
  const mondayOffset = (base.getUTCDay() + 6) % 7;
  return QUICK_SET_OFFSETS[kind].map((offset) => {
    const d = new Date(base);
    d.setUTCDate(base.getUTCDate() - mondayOffset + offset);
    return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
  });
}

function wallClock(iso: string): { date: string; minutes: number } | null {
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  if (!m) return null;
  return { date: m[1], minutes: Number(m[2]) * 60 + Number(m[3]) };
}

/**
 * Whether `existing` already has a window on `date` that overlaps
 * `start`–`end` (`HH:MM`). Compares the stored wall-clock strings directly.
 */
export function overlapsExisting(
  existing: Pick<EmployeeAvailability, "start_time" | "end_time">[],
  date: string,
  start: string,
  end: string,
): boolean {
  const [sh, sm] = start.split(":").map(Number);
  const [eh, em] = end.split(":").map(Number);
  const s = sh * 60 + sm;
  const e = eh * 60 + em;
  return existing.some((a) => {
    const as = wallClock(a.start_time);
    const ae = wallClock(a.end_time);
    if (!as || !ae || as.date !== date) return false;
    // A window ending on a later day runs to the end of `date`.
    const aEnd = ae.date === date ? ae.minutes : 24 * 60;
    return as.minutes < e && s < aEnd;
  });
}
