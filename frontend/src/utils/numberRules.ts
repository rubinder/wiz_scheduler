/** Helpers for the number lists page (#259 step 7). Whether an entry is
 * active, and whether a call is screened, are server answers: these only
 * format them and check the form before sending. */
import type { NumberRuleIn, NumberRuleOut, NumberRulePatch } from "../types/reservations";
import { dateParts } from "./availabilityDates";

/** Same shape the server accepts (screening.valid_e164). */
const E164 = /^\+[1-9][0-9]{1,14}$/;

/** The number as E.164 once spaces, dashes, dots and brackets are dropped, or null. */
export function normalizeE164(input: string): string | null {
  const compact = input.trim().replace(/[\s\-.()]/g, "");
  return E164.test(compact) ? compact : null;
}

/** Whether to show the E.164 format hint as an error: something typed, and not a valid number. */
export function showE164Hint(input: string): boolean {
  return input.trim() !== "" && normalizeE164(input) === null;
}

export type RuleStatus = "active" | "expired";

export function ruleStatus(rule: Pick<NumberRuleOut, "active">): RuleStatus {
  return rule.active ? "active" : "expired";
}

/** The expiry as a local calendar date ("Dec 4, 2026"), or `never`. The
 * date string is split, never parsed as an instant, so it cannot shift a day. */
export function formatExpiry(expiresOn: string | null, locale: string, never: string): string {
  if (!expiresOn) return never;
  const { year, month, day } = dateParts(expiresOn);
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, day)),
  );
}

/** Company-wide entries (no location) are admin-only to change; the server enforces it too. */
export function canEditRule(isAdmin: boolean, rule: Pick<NumberRuleOut, "location_id">): boolean {
  return isAdmin || rule.location_id !== null;
}

export interface RuleForm {
  number: string;
  action: "allow" | "block";
  /** A location id, or "" for every location. */
  scope: string;
  /** "YYYY-MM-DD", or "" for never. */
  expiresOn: string;
  note: string;
}

export const NOTE_MAX = 200;

export function emptyRuleForm(locationId: string): RuleForm {
  return { number: "", action: "block", scope: locationId, expiresOn: "", note: "" };
}

export function ruleToForm(rule: NumberRuleOut): RuleForm {
  return {
    number: rule.e164,
    action: rule.action,
    scope: rule.location_id ?? "",
    expiresOn: rule.expires_on ?? "",
    note: rule.note ?? "",
  };
}

/** The create body, or null while the number is not E.164 or the note is too long. */
export function formToCreate(form: RuleForm): NumberRuleIn | null {
  const e164 = normalizeE164(form.number);
  if (!e164 || form.note.length > NOTE_MAX) return null;
  return {
    e164,
    action: form.action,
    location_id: form.scope || null,
    expires_on: form.expiresOn || null,
    note: form.note.trim() || null,
  };
}

/** The edit body: action, expiry and note only. */
export function formToPatch(form: RuleForm): NumberRulePatch | null {
  if (form.note.length > NOTE_MAX) return null;
  return { action: form.action, expires_on: form.expiresOn || null, note: form.note.trim() || null };
}

/** The `reservations.numbers` message key for a number-list error code, or null for the shared ones. */
export function numberErrorKey(
  code: string | null,
): "duplicateRule" | "expiryInPast" | "noCallerNumber" | "adminOnly" | null {
  switch (code) {
    case "duplicate_rule":
      return "duplicateRule";
    case "expiry_in_past":
      return "expiryInPast";
    case "no_caller_number":
      return "noCallerNumber";
    case "admin_only":
      return "adminOnly";
    default:
      return null;
  }
}
