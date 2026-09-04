// ── Auth ──

export interface RegisterRequest {
  email: string;
  // Provide exactly one of `password` or `google_id_token`.
  password?: string;
  google_id_token?: string;
  full_name: string;
  company_name: string;
  privacy_accepted?: boolean;
  terms_accepted?: boolean;
  stripe_session_id?: string;
  // Opaque per-browser id from getDeviceId(). Recorded as a signup signal
  // only — the backend never denies anything on it.
  device_id?: string;
}

export interface LoginRequest {
  email: string;
  password: string;
  ownership_group_id?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface User {
  id: string;
  company_id: string;
  email: string;
  full_name: string | null;
  user_role: string;
  ownership_group_id: string | null;
  is_demo: boolean;
  has_google: boolean;
  // False blocks schedule generation and nothing else.
  email_verified: boolean;
}

// ── Company ──

export interface Company {
  id: string;
  name: string;
  slug: string;
  ownership_group_id: string | null;
  created_at: string;
}

// ── Ownership Group ──

export interface OwnershipGroup {
  id: string;
  name: string;
  created_at: string;
}

export interface CompanySummary {
  id: string;
  name: string;
  slug: string;
}

export interface OwnershipGroupCompaniesResponse {
  ownership_group: OwnershipGroup;
  companies: CompanySummary[];
}

// ── Region ──

export interface Region {
  id: string;
  company_id: string;
  name: string;
  geo_bounds: Record<string, unknown> | null;
}

// ── Location ──

export interface Location {
  id: string;
  company_id: string;
  region_id: string;
  name: string;
  address: string | null;
  geo_coord: Record<string, unknown> | null;
  timezone: string;
  /** Minimum rest hours between shifts on different days (NYC Fair Workweek
   * clopening rule = 11). null = no constraint. */
  min_rest_hours: number | null;
}

// ── Role ──

export interface Role {
  id: string;
  company_id: string;
  name: string;
  description: string | null;
  external_id: string | null;
}

// ── Condensed Role ──

export interface CondensedRoleMapping {
  role_id: string;
  role_name: string;
}

export interface CondensedRole {
  id: string;
  company_id: string;
  name: string;
  roles: CondensedRoleMapping[];
}

// ── Employee ──

export interface Employee {
  id: string;
  company_id: string;
  user_id: string | null;
  full_name: string;
  email: string | null;
  location_ids: string[] | null;
  max_hours_per_week: number | null;
  roles: EmployeeRole[];
  company_ids: string[];
}

export interface EmployeeRole {
  id: string;
  role_id: string;
  skill_level: number;
}

export interface EmployeeMeRole {
  role_id: string;
  role_name: string;
}

export interface EmployeeMeLocation {
  location_id: string;
  location_name: string;
}

export interface EmployeeMeProfile {
  id: string;
  full_name: string;
  email: string | null;
  roles: EmployeeMeRole[];
  locations: EmployeeMeLocation[];
}

export interface EmployeeAffinity {
  id: string;
  employee_id: string;
  target_employee_id: string;
  level: number;
  entry_date: string;
  expiration_date: string | null;
}

export interface EmployeeAvailability {
  id: string;
  company_id: string;
  employee_id: string;
  year: number;
  month: number;
  day: number;
  start_time: string;
  end_time: string;
}

export interface EmployeeDayBlackout {
  id: string;
  company_id: string;
  employee_id: string;
  day_of_week: number; // 0 = Monday ... 6 = Sunday
  start_time: string;  // "HH:MM"
  end_time: string;    // "HH:MM"
}

// ── Weighted scheduling preferences ──
//
// Shared weight rule across all three: 0.0-1.0 in increments of 0.1. 1.0 is
// a hard rule (a slot with no surviving candidate is emitted VACANT);
// anything below is a soft, scored preference. An employee with no row for
// a given resource is equivalent to weight 0 (no effect).

export interface EmployeeDayPreference {
  id: string;
  company_id: string;
  employee_id: string;
  day_of_week: number; // 0 = Monday ... 6 = Sunday
  weight: number;
}

export interface EmployeeHourRangePreference {
  id: string;
  company_id: string;
  employee_id: string;
  start_time: string; // "HH:MM"
  end_time: string;   // "HH:MM"
  weight: number;
}

export interface EmployeeHourRangeCap {
  id: string;
  company_id: string;
  employee_id: string;
  start_time: string; // "HH:MM"
  end_time: string;   // "HH:MM"
  max_per_week: number;
  weight: number;
}

// ── Shift Template ──

export interface ShiftTemplate {
  id: string;
  company_id: string;
  location_id: string;
  name: string;
  weekly_schedule: Record<string, unknown>[];
  specific_date: string | null;
}

// ── Schedule ──

export interface ShiftSchedule {
  id: string;
  company_id: string;
  location_id: string;
  week_start_date: string;
  status: string;
  created_at: string;
  shifts: Shift[];
}

export interface Shift {
  id: string;
  shift_schedule_id: string;
  location_id: string;
  employee_id: string;
  role_id: string;
  role_name: string;
  date: string;
  start_time: string;
  end_time: string;
}

// ── Scheduling streaming results ──

export interface PreferenceViolation {
  kind: "day" | "hour_range" | "cap";
  weight: number;
  unavoidable: boolean;
  days?: number[];
  start_time?: string;
  end_time?: string;
  max_per_week?: number;
}

export interface PreferenceSummary {
  shifts_against_preference: number;
  unavoidable: number;
  roster_thin: boolean;
}

export interface ShiftAssignment {
  employee_id: string;
  employee_name: string;
  role_id: string;
  role_name: string;
  location_id: string;
  date: string;
  start_time: string;
  end_time: string;
  status: string;
  preference_violations?: PreferenceViolation[];
}

export interface LocationResult {
  location_id: string;
  location_name: string;
  shifts: ShiftAssignment[];
  errors: string[];
  status: string;
  schedule_id?: string;
  preference_summary?: PreferenceSummary | null;
}

// ── Invites ──

export interface InviteResponse {
  id: string;
  employee_id: string;
  email: string;
  token: string;
  invite_url: string;
  status: string;
  created_at: string;
  expires_at: string;
}

export interface InviteInfoResponse {
  employee_name: string;
  email: string;
  company_name: string;
}

export interface AcceptInviteResponse {
  access_token: string;
  token_type: string;
}

export interface InviteStatusResponse {
  id: string;
  employee_id: string;
  employee_name: string;
  email: string;
  status: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
}

// ── Bulk upload ──

export interface BulkUploadResponse {
  created: number;
  skipped: number;
  errors: string[];
}

// ── Manager invites ──

export interface ManagerInvite {
  id: string;
  email: string;
  status: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_company_id: string | null;
}

export interface ManagerInviteInfo {
  email: string;
  group_name: string;
  expired: boolean;
  companies: { id: string; name: string }[];
}

// ── Special Hours ──

export interface SpecialHoursDay {
  id: string;
  company_id: string;
  location_id: string;
  date: string;          // YYYY-MM-DD
  open_time: string;     // HH:MM:SS
  close_time: string;    // HH:MM:SS
  label: string | null;
  shift_template_id: string | null;
  created_at: string;
}

// ── Plan (free tier) ──

export interface PlanLimitCount {
  count: number;
  limit: number | null; // null == unlimited
}

export interface PlanState {
  plan: "free" | "paid";
  canceled_at: string | null;
  locations: PlanLimitCount;
  employees: PlanLimitCount;
  schedules: PlanLimitCount;
  over_limit: boolean;
  can_generate_local: boolean;
  can_generate_ai: boolean;
  block_reason: string | null;
}

// ── Check-In ──

export interface CheckInQr {
  counter: number;
  svg: string;
  checked_in_today: number;
}

export type CheckInStatus =
  | "matched"
  | "no_shift"
  | "wrong_location"
  | "duplicate";

export interface CheckInResult {
  id: string;
  status: CheckInStatus;
  checked_in_at: string;
  local_date: string;
  /** Signed: negative early, positive late. Null when no shift matched. */
  minutes_from_start: number | null;
  shift_id: string | null;
}

export interface CheckInReportRow {
  id: string;
  employee_id: string;
  employee_name: string;
  location_id: string;
  checked_in_at: string;
  local_date: string;
  status: CheckInStatus;
  minutes_from_start: number | null;
}

export interface CheckInReport {
  rows: CheckInReportRow[];
  retention_days: number;
}
