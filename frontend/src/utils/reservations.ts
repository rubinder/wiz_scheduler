/** Helpers for the reservations manager UI. Times are the restaurant's local time. */

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
