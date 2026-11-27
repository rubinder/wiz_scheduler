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

/** An AI floor-plan draft (decision 101); `result` loads into the editor unsaved. */
export interface FloorDraftOut {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  result: FloorIn | null;
  warnings: string[];
  confidence: "low" | "medium" | "high" | null;
  /** What was debited from AI credits; null until done, and on failure. */
  cost_usd: number | null;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
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

/** Phone tuning knobs: API-only, no editor UI; carried through a save unchanged. */
export interface VoiceKnobs {
  voice_reply_max_sentences: number;
  voice_read_back: boolean;
  voice_confirm_style: "question" | "statement";
}

/** Where the AI host books (#259 step 9). Admin-only in the rules editor. */
export type BookingProviderName = "wizscheduler" | "mock_external";

/** The mock external provider's knobs (rules.mock_external). Admin-only. */
export interface MockExternalSettings {
  /** 1–500 */
  max_covers_per_slot: number;
  /** 0–3000 */
  latency_ms: number;
  /** 0–0.5 */
  failure_rate: number;
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
  voice?: VoiceKnobs;
  staff_transfer_number: string | null;
  /** The AI host stops taking new conversations at this location once today's spend reaches it (min 1). */
  daily_spend_cap_usd: number;
  /** Omitted on save by a regular manager: the server keeps the stored values (403 admin_only on a change). */
  provider?: BookingProviderName;
  mock_external?: MockExternalSettings;
  /** The greeting says the host is automated (default true). Admin-only, omitted like `provider`. */
  disclose_automated?: boolean;
  /** Custom conversation flows per channel; null = the default flow (decision 102). Omitted by the rules editor. */
  flows?: Flows;
  /** Regular managers an admin allowed to edit `flows`. Admin-only. */
  flow_editor_ids?: string[];
}

// ── Conversation flows (decision 102) ──

export type FlowChannel = "web" | "voice";
export type FlowCollect = "party_size" | "date_time" | "name" | "phone" | "occasion" | "free_text";
export type FlowStepKind = "ask" | "choice" | "book" | "change_or_cancel" | "say" | "handoff" | "end";

export interface FlowOption {
  label: string;
  next: string;
}

interface FlowStepBase {
  /** Slug, at most 40 characters. */
  id: string;
  /** Editor label. */
  title: string;
}

export type FlowStep = FlowStepBase &
  (
    | { kind: "ask"; prompt: string; collect: FlowCollect; next: string }
    | { kind: "choice"; prompt: string; options: FlowOption[] }
    | { kind: "book"; next: string | null }
    | { kind: "change_or_cancel" }
    | { kind: "say"; message: string; next: string }
    | { kind: "handoff"; reason: string }
    | { kind: "end"; message: string }
  );

export interface Flow {
  version: 1;
  start: string;
  steps: FlowStep[];
}

export interface Flows {
  web: Flow | null;
  voice: Flow | null;
}

/** PUT .../flows. `flow_editor_ids` is admin-only; omitted, the stored list is kept. */
export interface FlowsIn {
  flows: Flows;
  flow_editor_ids?: string[];
  /** The flows_version the page loaded; a stale one is 409 flow_conflict. */
  flows_version: number;
}

/** One `invalid_flow` problem (the server's `problems`, or the editor's mirror). */
export interface FlowProblem {
  code: string;
  step: string | null;
  field: string | null;
  channel?: FlowChannel;
}

export interface RulesOut {
  rules: RulesSchema;
  version: number;
  /** Bumped only when the flows or their editors change. */
  flows_version: number;
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
  /** Booked by the AI host in an external provider: read-only for staff, never has tables. */
  external: boolean;
  provider: BookingProviderName | string;
}

/** GET /reservations/config. conversation_outcomes mirrors agent/models.py OUTCOMES (served, so it cannot drift). */
export interface ReservationsConfig {
  enabled: boolean;
  /** Whether the rules editor may offer the mock external provider. */
  mock_provider_allowed?: boolean;
  /** The AI host phone line is on; the call capacity card shows only then. */
  voice_enabled?: boolean;
  conversation_outcomes: string[];
}

/** One AI host conversation in the manager list. cost_usd is a decimal string. */
export interface ConversationOut {
  id: string;
  channel: "web" | "voice" | string;
  started_at: string;
  ended_at: string | null;
  outcome: string | null;
  turn_count: number;
  cost_usd: string;
  reservation_id: string | null;
  /** The booking provider the conversation was fixed to; "mock_external" is the test double. */
  provider?: string;
}

export interface AgentEventOut {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  latency_ms: Record<string, unknown>;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: string;
  created_at: string;
}

export interface ConversationDetailOut extends ConversationOut {
  location_id: string;
  status: string;
  /** A phone call with a caller number: "Mark as spam" applies (the server decides). */
  spam_markable: boolean;
  events: AgentEventOut[];
}

export type NumberRuleAction = "allow" | "block";

/** A number-list entry. location_id null = every location of the company. */
export interface NumberRuleOut {
  id: string;
  e164: string;
  action: NumberRuleAction;
  location_id: string | null;
  expires_at: string | null;
  /** The local date the entry stops applying at the end of (YYYY-MM-DD), or null = never. */
  expires_on: string | null;
  note: string | null;
  created_by_name: string | null;
  created_at: string;
  /** Server-evaluated: not yet expired. */
  active: boolean;
}

/** POST .../mark-spam: the block entry; created false when it already existed. */
export interface MarkSpamOut extends NumberRuleOut {
  created: boolean;
}

export interface NumberRuleIn {
  e164: string;
  action: NumberRuleAction;
  location_id: string | null;
  expires_on: string | null;
  note: string | null;
}

/** Only action, expiry and note change; delete and recreate to change the number or scope. */
export interface NumberRulePatch {
  action?: NumberRuleAction;
  expires_on?: string | null;
  note?: string | null;
}

/** Calls screened over the last `days` days. */
export interface ScreeningStatsOut {
  days: number;
  blocked_list: number;
  blocked_ftc: number;
  answered: number;
}

/** One agent_daily_metrics row: one UTC day at one location. */
export interface InsightsDay {
  day: string;
  conversations: number;
  resolved: number;
  booked: number;
  handed_off: number;
  abandoned: number;
  failed: number;
  /** Conversations that looked for a slot: the booking conversion's denominator. */
  booking_intent: number;
  handoff_reasons: Record<string, number>;
  llm_p50_ms: number | null;
  llm_p95_ms: number | null;
  turn_p50_ms: number | null;
  turn_p95_ms: number | null;
  /** Decimal strings. */
  cost_usd: string;
  billed_usd: string;
  screened: number;
  blocked: number;
  covers_by_period: Record<string, number>;
}

/** GET /reservations/insights: `from`..`to` inclusive (UTC dates); days without a row are absent. */
export interface InsightsOut {
  location_id: string;
  from: string;
  to: string;
  days: InsightsDay[];
}

/** One diner's data at one location (POST .../diner-data). */
export interface DinerDataOut {
  reservations: ReservationOut[];
  conversations: ConversationDetailOut[];
  /** That number's number-list entries at this location and company-wide (location_id null). */
  number_rules: NumberRuleOut[];
}

/** What erasing one reservation by confirmation code did (POST .../diner-data/erase-by-code). */
export interface DinerCodeEraseOut {
  reservations_scrubbed: number;
  /** The external (mock provider) booking with that code, deleted. */
  external_bookings_deleted: number;
  conversations_deleted: number;
  conversations_closed_in_flight: number;
  conversations_still_in_flight: number;
}

/** What erasing one diner's data did (POST .../diner-data/erase). */
export interface DinerEraseOut {
  reservations_scrubbed: number;
  /** The diner's external (mock provider) bookings here, deleted. */
  external_bookings_deleted: number;
  conversations_deleted: number;
  /** Mid-turn conversations closed rather than deleted. */
  conversations_closed_in_flight: number;
  /** Held by a running turn: neither deleted nor closed; erase again later. */
  conversations_still_in_flight: number;
  lookup_failures_deleted: number;
  /** This location's number-list entries for the number. */
  number_rules_deleted: number;
  /** Company-wide entries for the number, never erased here (an admin removes them in Number lists). */
  company_rules_remaining: number;
}
