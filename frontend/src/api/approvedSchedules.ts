import type { PreferenceSummary, PreferenceViolation, ShiftAssignment } from "../types";
import { apiFetch, ApiError } from "./client";
import { ScheduleLockedError } from "../hooks/useScheduleStream";

/** One location's schedule for a week, as stored after generation. */
export interface WeekSchedule {
  id: string;
  company_id: string;
  location_id: string;
  week_start_date: string;
  status: string;
  strategy: string | null;
  strategy_param: number | null;
  strategy_param2: number | null;
  created_at: string;
  shifts: WeekShift[];
  preference_summary: PreferenceSummary | null;
}

/** A materialised shift row. Distinct from ShiftAssignment: these are real
 *  rows created at approval time, so they carry an id and no status. */
export interface WeekShift {
  id: string;
  shift_schedule_id: string;
  location_id: string;
  employee_id: string;
  employee_name: string;
  role_id: string;
  role_name: string;
  date: string;
  start_time: string;
  end_time: string;
  preference_violations: PreferenceViolation[];
}

/** Approved schedules for the week beginning `weekStartDate`.
 *
 *  Filtered server-side: a week accumulates one draft per generation that was
 *  never approved, and those are dead weight here. */
export function getApprovedWeek(weekStartDate: string): Promise<WeekSchedule[]> {
  return apiFetch<WeekSchedule[]>(`/schedules/week/${weekStartDate}?status=approved`);
}

/** Adapt materialised shifts to what ScheduleGrid renders.
 *
 *  The grid is shared with the post-generation review, which works in
 *  ShiftAssignment. Approved shifts are always "ok" — a CONFLICT or VACANT
 *  shift never becomes a Shift row at approval time. Approved shifts carry
 *  their persisted preference_violations (#99). */
export function toAssignments(shifts: WeekShift[]): ShiftAssignment[] {
  return shifts.map((s) => ({
    employee_id: s.employee_id,
    employee_name: s.employee_name,
    role_id: s.role_id,
    role_name: s.role_name,
    location_id: s.location_id,
    date: s.date,
    start_time: s.start_time,
    end_time: s.end_time,
    status: "ok",
    preference_violations: s.preference_violations ?? [],
  }));
}

/** One of the three overridable warnings `PUT .../approved-shifts` can
 *  return. The edit has already been applied by the time a warning is
 *  returned — a 200 is not a request for confirmation. */
export interface EditWarning {
  code: "no_availability" | "already_booked" | "already_exported";
  shift_id: string | null;
  employee_id: string;
  detail: string;
}

/** One edit to an approved schedule's shifts. `shift_id` is omitted (or
 *  null) to create a new shift; `deleted: true` removes an existing one, in
 *  which case the other fields are ignored by the server. */
export interface ApprovedShiftEdit {
  shift_id?: string | null;
  deleted?: boolean;
  employee_id?: string;
  role_id?: string;
  date?: string;
  start_time?: string;
  end_time?: string;
}

/** Apply edits to an approved schedule's materialised `Shift` rows.
 *
 *  A 409 with code `schedule_locked` is normalised to `ScheduleLockedError`,
 *  matching how `schedules.ts` handles the same lock — the caller can
 *  `instanceof` it regardless of which endpoint produced it. The other two
 *  refusal codes (`shift_locked_by_checkin` on 409, `invalid_edit` on 400)
 *  and the three warning codes are left on the thrown `ApiError` / the
 *  resolved response for the caller to inspect directly. */
export async function editApprovedShifts(
  scheduleId: string,
  edits: ApprovedShiftEdit[]
): Promise<{ applied: number; warnings: EditWarning[] }> {
  try {
    return await apiFetch(`/schedules/${scheduleId}/approved-shifts`, {
      method: "PUT",
      body: JSON.stringify({ edits }),
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const data = err.data as { code?: string; locked_by?: string; expires_at?: string } | undefined;
      if (data?.code === "schedule_locked") {
        throw new ScheduleLockedError(
          String(data.locked_by ?? "another manager"),
          new Date(String(data.expires_at ?? Date.now())),
        );
      }
    }
    throw err;
  }
}
