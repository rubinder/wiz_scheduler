import { apiFetch } from "./client";
import type { CheckInQr, CheckInReport, CheckInResult } from "../types";

export function getCheckInQr(locationId: string): Promise<CheckInQr> {
  return apiFetch<CheckInQr>(
    `/check-ins/qr?location_id=${encodeURIComponent(locationId)}`
  );
}

export function submitCheckIn(
  token: string,
  locationId: string
): Promise<CheckInResult> {
  return apiFetch<CheckInResult>("/check-ins", {
    method: "POST",
    body: JSON.stringify({ token, location_id: locationId }),
  });
}

export function getCheckInReport(employeeId?: string): Promise<CheckInReport> {
  const q = employeeId
    ? `?employee_id=${encodeURIComponent(employeeId)}`
    : "";
  return apiFetch<CheckInReport>(`/check-ins/report${q}`);
}
