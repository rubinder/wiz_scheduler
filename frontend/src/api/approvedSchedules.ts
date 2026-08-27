import type { ShiftAssignment } from "../types";
import { apiFetch } from "./client";

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
 *  shift never becomes a Shift row at approval time. */
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
  }));
}
