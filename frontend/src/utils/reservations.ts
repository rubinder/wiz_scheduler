/** Helpers for the reservations manager UI. Times are the restaurant's local time. */
import type { DateOverride, Persona, RulesSchema, ServicePeriod } from "../types/reservations";


/** Hour:minute of an instant, in the restaurant's zone (never the browser's). */
export function formatLocalTime(iso: string, timeZone: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

/** "YYYY-MM-DD" of `now` in `timeZone`. */
export function localDateString(now: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

const KNOWN_ERRORS = new Set([
  "slot_unavailable",
  "hold_expired",
  "party_too_large",
  "invalid_transition",
  "not_found",
  "invalid_party_size",
  "table_has_bookings",
  "duplicate_label",
  "duplicate_id",
  "bad_seats",
  "bad_combination",
  "unknown_section",
  "unknown_id",
  "duplicate_section_key",
]);

/** Maps an ApiError.data detail ({code, message}) to an i18n key. */
export function errorKey(detail: unknown): string {
  const code =
    detail && typeof detail === "object" ? (detail as { code?: unknown }).code : undefined;
  return typeof code === "string" && KNOWN_ERRORS.has(code)
    ? `reservations.errors.${code}`
    : "reservations.errors.generic";
}

/** The rules editor's state: every number is the raw input string, so blank is representable. */
export interface RulesForm {
  service_periods: ServicePeriod[];
  slot_interval_minutes: string;
  durations: { max_party: string; minutes: string; churn_minutes: string }[];
  max_concurrent_covers: string;
  max_covers_per_slot: string;
  date_overrides: DateOverride[];
  max_self_book_party: string;
  max_party_size: string;
  booking_horizon_days: string;
  cutoff_minutes: string;
  closures: string[];
  /** Not editable here; carried through unchanged so a save never drops it. */
  section_cover_limits: Record<string, number>;
  hold_timeout_minutes: string;
  pending_hold_hours: string;
  persona: Persona;
  staff_transfer_number: string;
}

const hm = (t: string): string => t.slice(0, 5);
const str = (n: number | null): string => (n === null ? "" : String(n));
const num = (s: string): number => Number(s.trim());
const numOrNull = (s: string): number | null => (s.trim() === "" ? null : Number(s.trim()));

const periodToForm = (p: ServicePeriod): ServicePeriod => ({
  ...p,
  weekdays: [...p.weekdays],
  first_seating: hm(p.first_seating),
  last_seating: hm(p.last_seating),
});

export function payloadToRulesForm(r: RulesSchema): RulesForm {
  return {
    service_periods: r.service_periods.map(periodToForm),
    slot_interval_minutes: str(r.slot_interval_minutes),
    durations: r.durations.map((d) => ({
      max_party: str(d.max_party),
      minutes: str(d.minutes),
      churn_minutes: str(d.churn_minutes),
    })),
    max_concurrent_covers: str(r.max_concurrent_covers),
    max_covers_per_slot: str(r.max_covers_per_slot),
    date_overrides: r.date_overrides.map((o) => ({ ...o, periods: o.periods.map(periodToForm) })),
    max_self_book_party: str(r.max_self_book_party),
    max_party_size: str(r.max_party_size),
    booking_horizon_days: str(r.booking_horizon_days),
    cutoff_minutes: str(r.cutoff_minutes),
    closures: [...r.closures],
    section_cover_limits: { ...r.section_cover_limits },
    hold_timeout_minutes: str(r.hold_timeout_minutes),
    pending_hold_hours: str(r.pending_hold_hours),
    persona: { ...r.persona, facts: [...r.persona.facts] },
    staff_transfer_number: r.staff_transfer_number ?? "",
  };
}

/** Times stay "HH:MM" (the server accepts it); blank capacity, pacing and transfer number become null. */
export function rulesFormToPayload(f: RulesForm): RulesSchema {
  return {
    service_periods: f.service_periods.map(periodToForm),
    slot_interval_minutes: num(f.slot_interval_minutes),
    durations: f.durations.map((d) => ({
      max_party: num(d.max_party),
      minutes: num(d.minutes),
      churn_minutes: num(d.churn_minutes),
    })),
    max_concurrent_covers: numOrNull(f.max_concurrent_covers),
    max_covers_per_slot: numOrNull(f.max_covers_per_slot),
    date_overrides: f.date_overrides.map((o) => ({ ...o, periods: o.periods.map(periodToForm) })),
    max_self_book_party: num(f.max_self_book_party),
    max_party_size: num(f.max_party_size),
    booking_horizon_days: num(f.booking_horizon_days),
    cutoff_minutes: num(f.cutoff_minutes),
    closures: [...f.closures],
    section_cover_limits: { ...f.section_cover_limits },
    hold_timeout_minutes: num(f.hold_timeout_minutes),
    pending_hold_hours: num(f.pending_hold_hours),
    persona: { ...f.persona, facts: [...f.persona.facts] },
    staff_transfer_number: f.staff_transfer_number.trim() === "" ? null : f.staff_transfer_number.trim(),
  };
}
