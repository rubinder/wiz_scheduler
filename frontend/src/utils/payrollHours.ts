/** The two pure formatters the payroll page needs.
 *
 *  Kept out of the component so they can be unit-tested, per the house
 *  pattern (preferenceText.test.ts, shiftTime.test.ts). Neither constructs a
 *  `Date` — nothing here touches a shift timestamp.
 */

/** Minutes as payroll hours, two decimal places: 480 -> "8.00".
 *
 *  Hours rather than minutes because that is what the CSV column and every
 *  payroll import template use, and the two must agree on screen and in the
 *  file. */
export function formatPaidHours(minutes: number): string {
  return (minutes / 60).toFixed(2);
}

export interface LatenessLabels {
  /** Carries a {minutes} placeholder. */
  early: string;
  /** Carries a {minutes} placeholder. */
  late: string;
  onTime: string;
}

/** Signed minutes-from-start as a sentence.
 *
 *  `null` renders as nothing at all: an attested entry has no scan, and
 *  reporting "on time" for an arrival nobody observed would state a fact we
 *  do not have. The labels are passed in because this string is localized
 *  into 19 languages. */
export function formatLateness(
  minutes: number | null,
  labels: LatenessLabels
): string {
  if (minutes === null) return "";
  if (minutes === 0) return labels.onTime;
  if (minutes < 0) return labels.early.replace("{minutes}", String(-minutes));
  return labels.late.replace("{minutes}", String(minutes));
}
