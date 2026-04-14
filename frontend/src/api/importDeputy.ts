import { apiFetch } from "./client";

export interface DeputyImportResult {
  locations: { created: number; updated: number; deleted: number };
  roles: { created: number; updated: number; deleted: number };
  employees: { created: number; updated: number; deleted: number };
  unavailability: { created: number; skipped: number; deleted: number };
  errors: string[];
}

export function importFromDeputy(
  accessToken: string,
  deputyBaseUrl: string
): Promise<DeputyImportResult> {
  return apiFetch<DeputyImportResult>("/import/deputy", {
    method: "POST",
    body: JSON.stringify({
      access_token: accessToken,
      deputy_base_url: deputyBaseUrl,
    }),
  });
}

export interface DeputyAvailImportResult {
  created: number;
  skipped: number;
  cleared: number;
  errors: string[];
}

export function importAvailabilitiesFromDeputy(
  accessToken: string,
  deputyBaseUrl: string,
  weekStart: string,
  weekEnd: string
): Promise<DeputyAvailImportResult> {
  return apiFetch<DeputyAvailImportResult>("/import/deputy/availabilities", {
    method: "POST",
    body: JSON.stringify({
      access_token: accessToken,
      deputy_base_url: deputyBaseUrl,
      week_start: weekStart,
      week_end: weekEnd,
    }),
  });
}
