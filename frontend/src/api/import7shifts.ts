import { apiFetch } from "./client";

export interface ImportResult {
  companies: { created: number; updated: number; deleted: number };
  locations: { created: number; updated: number; deleted: number };
  departments: { created: number; updated: number; deleted: number };
  roles: { created: number; updated: number; deleted: number };
  employees: { created: number; updated: number; deleted: number };
  user_assignments: { created: number; updated: number; deleted: number };
  errors: string[];
}

export function importFrom7Shifts(
  accessToken: string
): Promise<ImportResult> {
  return apiFetch<ImportResult>("/import/7shifts", {
    method: "POST",
    body: JSON.stringify({ access_token: accessToken }),
  });
}
