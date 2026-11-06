import type { ShiftAssignment, ShiftSchedule } from "../types";
import { apiFetch, apiStream, ApiError } from "./client";

export class ScheduleLockedError extends Error {
  constructor(
    public readonly lockedBy: string,
    public readonly expiresAt: Date,
    // The busy location (#180). Absent from older servers' 409s.
    public readonly locationName: string | null = null,
    // The busy location is locked only because it shares employees with
    // the one asked for (#222). False from older servers.
    public readonly sharedStaff: boolean = false,
  ) {
    super(`Schedule locked by ${lockedBy} until ${expiresAt.toISOString()}`);
    this.name = "ScheduleLockedError";
  }
}

/** The 409 `schedule_locked` detail as a ScheduleLockedError. */
export function scheduleLockedErrorFromDetail(detail: {
  locked_by?: unknown;
  expires_at?: unknown;
  location_name?: unknown;
  shared_staff?: unknown;
}): ScheduleLockedError {
  return new ScheduleLockedError(
    String(detail.locked_by ?? "another manager"),
    new Date(String(detail.expires_at ?? Date.now())),
    typeof detail.location_name === "string" && detail.location_name ? detail.location_name : null,
    detail.shared_staff === true,
  );
}

export interface GenerateOptions {
  useLocal?: boolean;
  strategy?: "random" | "rotation" | "rotation_history" | "max_hours";
  strategyParam?: number;
  strategyParam2?: number;
  numDays?: number;
  seniorityWeight?: number;
  payWeight?: number;
  overtimeWeight?: number;
  affinityWeight?: number;
}

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
        | { code?: string; locked_by?: string; expires_at?: string; location_name?: string | null; shared_staff?: boolean }
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

export function generateScheduleBody(
  weekStartDate: string,
  locationIds?: string[],
  templateIds?: string[],
  options?: GenerateOptions
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    week_start_date: weekStartDate,
  };
  if (locationIds && locationIds.length > 0) {
    body.location_ids = locationIds;
  }
  if (templateIds && templateIds.length > 0) {
    body.template_ids = templateIds;
  }
  if (options?.useLocal) {
    body.use_local = true;
    body.strategy = options.strategy || "random";
    if (options.strategyParam !== undefined) {
      body.strategy_param = options.strategyParam;
    }
    if (options.strategyParam2 !== undefined) {
      body.strategy_param2 = options.strategyParam2;
    }
  }
  if (options?.numDays && options.numDays !== 7) {
    body.num_days = options.numDays;
  }
  // Add signal weight parameters if provided
  if (options?.seniorityWeight !== undefined) {
    body.seniority_weight = options.seniorityWeight;
  }
  if (options?.payWeight !== undefined) {
    body.pay_weight = options.payWeight;
  }
  if (options?.overtimeWeight !== undefined) {
    body.overtime_weight = options.overtimeWeight;
  }
  if (options?.affinityWeight !== undefined) {
    body.affinity_weight = options.affinityWeight;
  }
  return body;
}

export async function generateScheduleStream(
  body: Record<string, unknown>,
  signal: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  try {
    return await apiStream("/schedules/generate", body, signal);
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const data = err.data as
        | { code?: string; locked_by?: string; expires_at?: string; location_name?: string | null; shared_staff?: boolean }
        | undefined;
      if (data?.code === "schedule_locked") {
        throw scheduleLockedErrorFromDetail(data);
      }
    }
    throw err;
  }
}
