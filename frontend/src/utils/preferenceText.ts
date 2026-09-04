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

// All-occurrence placeholder substitution without relying on ES2021's
// String.prototype.replaceAll (the frontend's TS lib target stays ES2020).
const fill = (tpl: string, key: string, val: string) => tpl.split(key).join(val);

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
      line = fill(s.prefDay, "{days}", days);
    } else if (v.kind === "cap") {
      line = fill(
        fill(
          fill(s.prefCap, "{n}", String(v.max_per_week ?? 0)),
          "{start}",
          hm(v.start_time),
        ),
        "{end}",
        hm(v.end_time),
      );
    } else {
      line = fill(fill(s.prefHourRange, "{start}", hm(v.start_time)), "{end}", hm(v.end_time));
    }
    return v.unavoidable ? `${line} — ${s.prefUnavoidable}` : line;
  });
}

export function rosterThinMessage(summary: PreferenceSummary, template: string): string {
  return fill(
    fill(template, "{unavoidable}", String(summary.unavoidable)),
    "{total}",
    String(summary.shifts_against_preference),
  );
}
