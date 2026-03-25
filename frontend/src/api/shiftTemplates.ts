import type { ShiftTemplate } from "../types";
import { apiFetch } from "./client";

export function listShiftTemplates(): Promise<ShiftTemplate[]> {
  return apiFetch<ShiftTemplate[]>("/shift-templates/");
}

export function createShiftTemplate(body: {
  location_id: string;
  name: string;
  weekly_schedule: Record<string, unknown>[];
}): Promise<ShiftTemplate> {
  return apiFetch<ShiftTemplate>("/shift-templates/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateShiftTemplate(
  id: string,
  body: {
    name?: string;
    weekly_schedule?: Record<string, unknown>[];
  }
): Promise<ShiftTemplate> {
  return apiFetch<ShiftTemplate>(`/shift-templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteShiftTemplate(id: string): Promise<void> {
  return apiFetch<void>(`/shift-templates/${id}`, { method: "DELETE" });
}
