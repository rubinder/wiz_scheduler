/** Mirrors backend/schemas/reservations.py and backend/reservations/{floor,rules}.py. */

export interface SectionIO {
  id: string | null;
  key: string;
  name: string;
  sort_order: number;
}

export interface TableIO {
  id: string | null;
  label: string;
  section_key: string | null;
  min_seats: number;
  max_seats: number;
  combinable: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number;
}

export interface CombinationIO {
  id: string | null;
  name: string;
  table_labels: string[];
}

export interface CombinationOut extends CombinationIO {
  min_seats: number;
  max_seats: number;
}

export interface FloorIn {
  sections: SectionIO[];
  tables: TableIO[];
  combinations: CombinationIO[];
}

export interface FloorOut {
  sections: SectionIO[];
  tables: TableIO[];
  combinations: CombinationOut[];
}

export interface ServicePeriod {
  name: string;
  /** 0 = Monday .. 6 = Sunday. */
  weekdays: number[];
  /** "HH:MM:SS" local time. */
  first_seating: string;
  last_seating: string;
}

export interface DurationBand {
  max_party: number;
  minutes: number;
  churn_minutes: number;
}

export interface DateOverride {
  /** "YYYY-MM-DD" */
  date: string;
  periods: ServicePeriod[];
  note: string;
}

export interface Persona {
  voice_id: string;
  greeting: string;
  facts: string[];
}

export interface RulesSchema {
  service_periods: ServicePeriod[];
  slot_interval_minutes: number;
  durations: DurationBand[];
  max_concurrent_covers: number | null;
  max_covers_per_slot: number | null;
  date_overrides: DateOverride[];
  max_self_book_party: number;
  max_party_size: number;
  booking_horizon_days: number;
  cutoff_minutes: number;
  closures: string[];
  section_cover_limits: Record<string, number>;
  hold_timeout_minutes: number;
  pending_hold_hours: number;
  persona: Persona;
  staff_transfer_number: string | null;
}

export interface RulesOut {
  rules: RulesSchema;
  version: number;
}

export interface SlotOut {
  /** ISO with offset; send back unchanged when booking. */
  start: string;
  end: string;
  table_ids: string[];
}

export interface DinerIn {
  name: string;
  phone?: string | null;
  email?: string | null;
}

export interface BookingIn {
  party_size: number;
  start: string;
  diner: DinerIn;
  notes?: string | null;
}

export interface ReservationPatch {
  party_size?: number;
  start?: string;
  notes?: string | null;
}

export interface WalkInIn {
  party_size: number;
  name?: string | null;
  phone?: string | null;
  notes?: string | null;
}

/** Mirrors backend/reservations/models.py ALL_STATUSES. */
export type ReservationStatus =
  | "held"
  | "pending_approval"
  | "booked"
  | "seated"
  | "completed"
  | "no_show"
  | "cancelled"
  | "declined"
  | "expired";

export type SettableStatus = "seated" | "completed" | "no_show" | "cancelled";

export interface ReservationOut {
  id: string;
  location_id: string;
  party_size: number;
  start: string;
  end: string;
  status: ReservationStatus | string;
  tables: string[];
  diner_name: string | null;
  diner_phone: string | null;
  diner_email: string | null;
  notes: string | null;
  source: string;
  confirmation_code: string | null;
  held_until: string | null;
}
