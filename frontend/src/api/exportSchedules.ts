import { apiFetch } from "./client";

export interface ExportShift {
  id: string;
  employee_id: string;
  employee_name: string;
  role_id: string;
  role_name: string;
  date: string;
  start_time: string;
  end_time: string;
  exported_at: string | null;
}

export interface ApprovedScheduleItem {
  schedule_id: string;
  location_id: string;
  location_name: string;
  week_start_date: string;
  shifts: ExportShift[];
  total_shifts: number;
  exported_shifts: number;
}

export interface Export7ShiftsResult {
  exported: number;
  failed: number;
  errors: string[];
}

export function listApprovedSchedules(
  weekStartDate?: string
): Promise<ApprovedScheduleItem[]> {
  const qs = weekStartDate ? `?week_start_date=${weekStartDate}` : "";
  return apiFetch<ApprovedScheduleItem[]>(`/export/approved-schedules${qs}`);
}

export function exportTo7Shifts(
  accessToken: string,
  shiftIds: string[]
): Promise<Export7ShiftsResult> {
  return apiFetch<Export7ShiftsResult>("/export/7shifts", {
    method: "POST",
    body: JSON.stringify({ access_token: accessToken, shift_ids: shiftIds }),
  });
}

export function getJsonlDownloadUrl(scheduleId: string): string {
  const token = localStorage.getItem("token");
  return `/api/v1/export/jsonl/${scheduleId}?token=${token}`;
}
