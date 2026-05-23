import { apiFetch } from "./client";
import type { SpecialHoursDay } from "../types";

export interface CreateSpecialHoursDayArgs {
  location_id: string;
  date: string;
  open_time: string;
  close_time: string;
  label?: string | null;
  draft_template_id?: string | null;
}

export interface UpdateSpecialHoursDayArgs {
  date?: string;
  open_time?: string;
  close_time?: string;
  label?: string | null;
}

export function listSpecialHours(params?: {
  location_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<SpecialHoursDay[]> {
  const qs = new URLSearchParams();
  if (params?.location_id) qs.set("location_id", params.location_id);
  if (params?.from_date) qs.set("from_date", params.from_date);
  if (params?.to_date) qs.set("to_date", params.to_date);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<SpecialHoursDay[]>(`/special-hours/${suffix}`);
}

export function createSpecialHoursDay(
  args: CreateSpecialHoursDayArgs,
): Promise<SpecialHoursDay> {
  return apiFetch<SpecialHoursDay>("/special-hours/", {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export function updateSpecialHoursDay(
  id: string,
  args: UpdateSpecialHoursDayArgs,
): Promise<SpecialHoursDay> {
  return apiFetch<SpecialHoursDay>(`/special-hours/${id}`, {
    method: "PUT",
    body: JSON.stringify(args),
  });
}

export function deleteSpecialHoursDay(id: string): Promise<void> {
  return apiFetch<void>(`/special-hours/${id}`, { method: "DELETE" });
}
