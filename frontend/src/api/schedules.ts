import type { ShiftAssignment, ShiftSchedule } from "../types";
import { apiFetch } from "./client";

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
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/schedules/${scheduleId}/shifts`, {
    method: "PUT",
    body: JSON.stringify({ shifts }),
  });
}

export function approveSchedule(id: string): Promise<ShiftSchedule> {
  return apiFetch<ShiftSchedule>(`/schedules/${id}/approve`, {
    method: "POST",
  });
}

export function rejectSchedule(id: string): Promise<ShiftSchedule> {
  return apiFetch<ShiftSchedule>(`/schedules/${id}/reject`, {
    method: "POST",
  });
}
