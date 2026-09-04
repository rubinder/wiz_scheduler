/**
 * Words for the preference asterisk (#99).
 *
 * Pure so it can be tested without a DOM. Times are sliced from the stored
 * HH:MM strings, never parsed through Date — a preference is a wall-clock
 * face, and Date would re-anchor it to the viewer's zone (#92).
 */
import type { PreferenceSummary, PreferenceViolation } from "../types";

export interface PreferenceStrings {
  prefDay: string;
  prefHourRange: string;
  prefCap: string;
  prefUnavoidable: string;
  /** Comma-separated short weekday names, Monday first. */
  weekdaysShort: string;
}

const hm = (t: string | undefined) => (t ?? "").slice(0, 5);

export function describeViolations(
  violations: PreferenceViolation[] | undefined,
  s: PreferenceStrings,
): string[] {
  if (!violations || violations.length === 0) return [];
  const names = s.weekdaysShort.split(",");
  return violations.map((v) => {
    let line: string;
    if (v.kind === "day") {
      const days = (v.days ?? []).map((d) => names[d] ?? String(d)).join(", ");
      line = s.prefDay.replace("{days}", days);
    } else if (v.kind === "cap") {
      line = s.prefCap
        .replace("{n}", String(v.max_per_week ?? 0))
        .replace("{start}", hm(v.start_time))
        .replace("{end}", hm(v.end_time));
    } else {
      line = s.prefHourRange.replace("{start}", hm(v.start_time)).replace("{end}", hm(v.end_time));
    }
    return v.unavoidable ? `${line} — ${s.prefUnavoidable}` : line;
  });
}

export function rosterThinMessage(summary: PreferenceSummary, template: string): string {
  return template
    .replace("{unavoidable}", String(summary.unavoidable))
    .replace("{total}", String(summary.shifts_against_preference));
}
