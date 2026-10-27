import type { ShiftAssignment, ShiftSchedule } from "../types";
import { apiFetch, ApiError } from "./client";
import { scheduleLockedErrorFromDetail } from "../hooks/useScheduleStream";

export function listSchedules(weekStartDate?: string): Promise<ShiftSchedule[]> {
  const qs = weekStartDate ? `?week_start_date=${weekStartDate}` : "";
  return apiFetch<ShiftSchedule[]>(`/schedules/${qs}`);
}

export function getSchedule(id: string): Promise<ShiftSchedule> {
  return apiFetch<ShiftSchedule>(`/schedules/${id}`);
}

export function updateShifts(
  scheduleId: string,
  shifts: ShiftAssignment[]
): Promise<{ ok: boolean; shifts: ShiftAssignment[] }> {
  return apiFetch<{ ok: boolean; shifts: ShiftAssignment[] }>(`/schedules/${scheduleId}/shifts`, {
    method: "PUT",
    body: JSON.stringify({ shifts }),
  });
}

export async function approveSchedule(id: string): Promise<ShiftSchedule> {
  try {
    return await apiFetch<ShiftSchedule>(`/schedules/${id}/approve`, {
      method: "POST",
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const data = err.data as
        | { code?: string; locked_by?: string; expires_at?: string; location_name?: string | null }
        | undefined;
      if (data?.code === "schedule_locked") {
        throw scheduleLockedErrorFromDetail(data);
      }
    }
    throw err;
  }
}

export function rejectSchedule(id: string): Promise<ShiftSchedule> {
  return apiFetch<ShiftSchedule>(`/schedules/${id}/reject`, {
    method: "POST",
  });
}
