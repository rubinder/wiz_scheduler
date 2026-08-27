import type {
  EmployeeDayPreference,
  EmployeeHourRangeCap,
  EmployeeHourRangePreference,
} from "../types";
import { apiFetch } from "./client";

// ── Day preferences ──

export function listDayPreferences(): Promise<EmployeeDayPreference[]> {
  return apiFetch<EmployeeDayPreference[]>("/scheduling-preferences/days");
}

export function createDayPreference(body: {
  employee_id: string;
  day_of_week: number;
  weight?: number;
}): Promise<EmployeeDayPreference> {
  return apiFetch<EmployeeDayPreference>("/scheduling-preferences/days", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateDayPreference(
  id: string,
  body: { day_of_week?: number; weight?: number }
): Promise<EmployeeDayPreference> {
  return apiFetch<EmployeeDayPreference>(`/scheduling-preferences/days/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteDayPreference(id: string): Promise<void> {
  return apiFetch<void>(`/scheduling-preferences/days/${id}`, {
    method: "DELETE",
  });
}

// ── Hour-range preferences ──

export function listHourRangePreferences(): Promise<EmployeeHourRangePreference[]> {
  return apiFetch<EmployeeHourRangePreference[]>(
    "/scheduling-preferences/hour-ranges"
  );
}

export function createHourRangePreference(body: {
  employee_id: string;
  start_time: string;
  end_time: string;
  weight?: number;
}): Promise<EmployeeHourRangePreference> {
  return apiFetch<EmployeeHourRangePreference>(
    "/scheduling-preferences/hour-ranges",
    {
      method: "POST",
      body: JSON.stringify(body),
    }
  );
}

export function updateHourRangePreference(
  id: string,
  body: { start_time?: string; end_time?: string; weight?: number }
): Promise<EmployeeHourRangePreference> {
  return apiFetch<EmployeeHourRangePreference>(
    `/scheduling-preferences/hour-ranges/${id}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    }
  );
}

export function deleteHourRangePreference(id: string): Promise<void> {
  return apiFetch<void>(`/scheduling-preferences/hour-ranges/${id}`, {
    method: "DELETE",
  });
}

// ── Hour-range caps ──

export function listHourRangeCaps(): Promise<EmployeeHourRangeCap[]> {
  return apiFetch<EmployeeHourRangeCap[]>("/scheduling-preferences/caps");
}

export function createHourRangeCap(body: {
  employee_id: string;
  start_time: string;
  end_time: string;
  max_per_week: number;
  weight?: number;
}): Promise<EmployeeHourRangeCap> {
  return apiFetch<EmployeeHourRangeCap>("/scheduling-preferences/caps", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateHourRangeCap(
  id: string,
  body: {
    start_time?: string;
    end_time?: string;
    max_per_week?: number;
    weight?: number;
  }
): Promise<EmployeeHourRangeCap> {
  return apiFetch<EmployeeHourRangeCap>(`/scheduling-preferences/caps/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteHourRangeCap(id: string): Promise<void> {
  return apiFetch<void>(`/scheduling-preferences/caps/${id}`, {
    method: "DELETE",
  });
}
