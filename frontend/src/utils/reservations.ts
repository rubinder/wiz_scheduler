/** Helpers for the reservations manager UI. Times are the restaurant's local time. */
import type {
  DateOverride,
  FloorIn,
  FloorOut,
  Persona,
  ReservationOut,
  RulesSchema,
  SectionIO,
  ServicePeriod,
  TableIO,
} from "../types/reservations";


/** Hour:minute of an instant, in the restaurant's zone (never the browser's). */
export function formatLocalTime(iso: string, timeZone: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

/** Short date plus time of an instant, in the restaurant's zone (e.g. "Fri, Dec 4, 7:00 PM"). */
export function formatLocalDateTime(iso: string, timeZone: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

/** A party size the server accepts (a whole number >= 1); input is the raw field string. */
export function validParty(s: string): boolean {
  if (s.trim() === "") return false;
  const n = Number(s);
  return Number.isInteger(n) && n >= 1;
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
  "invalid_phone",
  "phone_required",
  "code_exhausted",
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
  const code = errorCode(detail);
  return code ? `reservations.errors.${code}` : "reservations.errors.generic";
}

/** The known error code in an ApiError.data detail ({code, message}), or null. */
export function errorCode(detail: unknown): string | null {
  const code =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as { code?: unknown }).code
      : undefined;
  return typeof code === "string" && KNOWN_ERRORS.has(code) ? code : null;
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
  daily_spend_cap_usd: string;
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
    daily_spend_cap_usd: str(r.daily_spend_cap_usd),
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
    daily_spend_cap_usd: num(f.daily_spend_cap_usd),
  };
}

// ── Floor plan editor ──

/** The floor canvas in floor units (a 2-top is about 2 x 2). */
export const CANVAS = { width: 40, height: 25 } as const;

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
  rotation?: number;
}

/** Rounds to the nearest `step`; -0 becomes 0. */
export function snap(value: number, step = 0.5): number {
  return Math.round(value / step) * step + 0;
}

const quarterTurned = (rotation: number | undefined): boolean =>
  Math.abs(Math.round((rotation ?? 0) / 90)) % 2 === 1;

/** The drawn rectangle: the table is rotated about its centre. */
function footprint(t: Box): { x: number; y: number; w: number; h: number } {
  if (!quarterTurned(t.rotation)) return { x: t.x, y: t.y, w: t.w, h: t.h };
  return { x: t.x + (t.w - t.h) / 2, y: t.y + (t.h - t.w) / 2, w: t.h, h: t.w };
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** Position that keeps the whole drawn table inside CANVAS. */
export function clampTable(t: Box): { x: number; y: number } {
  const f = footprint(t);
  const ox = f.x - t.x;
  const oy = f.y - t.y;
  return {
    x: clamp(f.x, 0, CANVAS.width - f.w) - ox + 0,
    y: clamp(f.y, 0, CANVAS.height - f.h) - oy + 0,
  };
}

/** Position after moving by (dx, dy) floor units: snapped, then clamped. */
export function moveTable(t: Box, dx: number, dy: number): { x: number; y: number } {
  return clampTable({ ...t, x: snap(t.x + dx), y: snap(t.y + dy) });
}

const overlaps = (a: Box, b: Box): boolean => {
  const p = footprint(a);
  const q = footprint(b);
  return p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h;
};

/** Top-left of the first whole-unit grid spot (row by row) where a w x h table overlaps nothing. */
export function firstFreeSpot(tables: Box[], w = 2, h = 2): { x: number; y: number } {
  for (let y = 0; y + h <= CANVAS.height; y += 1) {
    for (let x = 0; x + w <= CANVAS.width; x += 1) {
      const box = { x, y, w, h };
      if (!tables.some((t) => overlaps(box, t))) return { x, y };
    }
  }
  return { x: 0, y: 0 };
}

/** "T1", "T2", ...: the first one not in `existing`. */
export function nextLabel(existing: string[]): string {
  const used = new Set(existing);
  let n = 1;
  while (used.has(`T${n}`)) n += 1;
  return `T${n}`;
}

/** Client-side key for a new section ("new-1", ...); saved sections use their id as key. */
export function nextSectionKey(existing: string[]): string {
  const used = new Set(existing);
  let n = 1;
  while (used.has(`new-${n}`)) n += 1;
  return `new-${n}`;
}

/** Labels for server table ids (table_has_bookings); an id we do not know is shown as is. */
export function labelsForIds(ids: string[], tables: Pick<TableIO, "id" | "label">[]): string[] {
  return ids.map((id) => tables.find((t) => t.id === id)?.label ?? id);
}

/** A table in the editor; `uid` is stable while the label is being edited. */
export interface EditorTable extends TableIO {
  uid: string;
}

/** Combinations point at tables by uid so a rename does not break them. */
export interface EditorCombination {
  /** Stable React key: the server id, or a client key for a new combination. */
  key: string;
  id: string | null;
  name: string;
  uids: string[];
  /** The server's computed range as of the last load or save; null once members change. */
  seats: { min: number; max: number } | null;
}

export interface EditorFloor {
  sections: SectionIO[];
  tables: EditorTable[];
  combinations: EditorCombination[];
}

export function floorToEditor(out: FloorOut): EditorFloor {
  const tables = out.tables.map((t, i) => ({ ...t, uid: t.id ?? `loaded-${i}` }));
  const uidByLabel = new Map(tables.map((t) => [t.label, t.uid]));
  return {
    sections: out.sections.map((s) => ({ ...s })),
    tables,
    combinations: out.combinations.map((c, i) => ({
      key: c.id ?? `loaded-c-${i}`,
      id: c.id,
      name: c.name,
      uids: c.table_labels.flatMap((l) => uidByLabel.get(l) ?? []),
      seats: { min: c.min_seats, max: c.max_seats },
    })),
  };
}

/** The whole floor for PUT: sections in display order, combinations by current label. */
export function editorToFloorIn(ed: EditorFloor): FloorIn {
  const labelByUid = new Map(ed.tables.map((t) => [t.uid, t.label]));
  return {
    sections: ed.sections.map((s, i) => ({ id: s.id, key: s.key, name: s.name, sort_order: i })),
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    tables: ed.tables.map(({ uid, ...t }) => t),
    combinations: ed.combinations.map((c) => ({
      id: c.id,
      name: c.name,
      table_labels: c.uids.flatMap((u) => labelByUid.get(u) ?? []),
    })),
  };
}

// ── Host view ──

/** "HH:MM" (24-hour) of an instant in the restaurant's zone; a grouping key, not a display string. */
function seatingKey(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("hour")}:${get("minute")}`;
}

/**
 * Off the host's list: gone for good, an agent's short-lived offer (held), or
 * (pending_approval) shown in the approvals section instead.
 */
const NOT_SEATING = new Set(["cancelled", "declined", "expired", "held", "pending_approval"]);

/**
 * Requests waiting for the restaurant's approval, ordered by start. A pending
 * row without a confirmation code is a large-party hold the diner has not
 * submitted yet: it is not a request, and the server refuses to approve it.
 */
export function pendingApprovals(reservations: ReservationOut[]): ReservationOut[] {
  return reservations
    .filter((r) => r.status === "pending_approval" && !!r.confirmation_code)
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}

/** The day's bookings grouped by seating time (restaurant time), ordered by start. */
export function groupBySeating(
  reservations: ReservationOut[],
  timeZone: string,
): Array<{ time: string; items: ReservationOut[] }> {
  const sorted = reservations
    .filter((r) => !NOT_SEATING.has(r.status))
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const groups: Array<{ time: string; items: ReservationOut[] }> = [];
  for (const r of sorted) {
    const time = seatingKey(r.start, timeZone);
    const last = groups[groups.length - 1];
    if (last && last.time === time) last.items.push(r);
    else groups.push({ time, items: [r] });
  }
  return groups;
}

export type TableState = "free" | "seated" | "upcoming";

const UPCOMING_MS = 60 * 60 * 1000;

/**
 * How to colour each table on the host's floor. DISPLAY ONLY: this never
 * decides whether a table can be booked; only the server's find_slots does.
 * `seated` if a seated party is on it; `upcoming` if a booked party is due on
 * it within 60 minutes of `now` (a late party not yet seated still counts
 * until its booking ends); else `free`.
 */
export function tableStates(
  tables: { label: string }[],
  reservations: ReservationOut[],
  now: Date,
): Record<string, TableState> {
  const out: Record<string, TableState> = {};
  for (const t of tables) out[t.label] = "free";
  const at = now.getTime();
  for (const r of reservations) {
    if (r.status === "booked") {
      const start = Date.parse(r.start);
      if (start - at > UPCOMING_MS || Date.parse(r.end) <= at) continue;
      for (const l of r.tables) if (out[l] === "free") out[l] = "upcoming";
    } else if (r.status === "seated") {
      for (const l of r.tables) if (l in out) out[l] = "seated";
    }
  }
  return out;
}
