import type { PayrollRetentionInfo } from "../types";

/** A "YYYY-MM-DD" calendar date in the viewer's locale, without moving the
 *  day. Built from its parts, not `new Date(iso)`, which parses as UTC
 *  midnight and reads as the previous day anywhere west of UTC. */
export function formatCalendarDate(iso: string, locale?: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString(locale);
}

/** The pre-sweep warning banner (#172), or null when there is nothing to
 *  warn about. The backend decides `warn` (records due AND the sweep within
 *  30 days); the page never re-derives it. */
export function retentionWarningText(
  template: string,
  info: PayrollRetentionInfo | null,
  locale?: string
): string | null {
  if (!info || !info.warn) return null;
  return template
    .replace("{count}", String(info.due_count))
    .replace("{cutoff}", formatCalendarDate(info.next_sweep_cutoff_date, locale))
    .replace("{date}", formatCalendarDate(info.next_sweep_date, locale));
}
