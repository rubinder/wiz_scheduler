import type { EmployeeAffinity } from "../types";
import { apiFetch } from "./client";

export function listAffinities(): Promise<EmployeeAffinity[]> {
  return apiFetch<EmployeeAffinity[]>("/affinities/");
}

export function createAffinity(body: {
  employee_id: string;
  target_employee_id: string;
  level: number;
  entry_date: string;
  expiration_date?: string | null;
}): Promise<EmployeeAffinity> {
  return apiFetch<EmployeeAffinity>("/affinities/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAffinity(
  id: string,
  body: {
    target_employee_id?: string;
    level?: number;
    entry_date?: string;
    expiration_date?: string | null;
  }
): Promise<EmployeeAffinity> {
  return apiFetch<EmployeeAffinity>(`/affinities/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAffinity(id: string): Promise<void> {
  return apiFetch<void>(`/affinities/${id}`, { method: "DELETE" });
}
