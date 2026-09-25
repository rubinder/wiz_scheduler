import { ApiError, apiFetch } from "./client";
import type {
  PayrollApproveResult,
  PayrollDeriveResult,
  PayrollEntriesResponse,
  PayrollExceptionsResponse,
  TimeEntryRow,
} from "../types";

/** Inclusive, "YYYY-MM-DD" both ends. At most 62 days, which the backend
 *  enforces with a 400 invalid_range. */
export interface PayrollRange {
  rangeStart: string;
  rangeEnd: string;
}

function rangeBody(
  range: PayrollRange,
  locationId?: string
): Record<string, unknown> {
  return {
    range_start: range.rangeStart,
    range_end: range.rangeEnd,
    location_id: locationId ?? null,
  };
}

function rangeQuery(range: PayrollRange, locationId?: string): string {
  const q = new URLSearchParams({
    range_start: range.rangeStart,
    range_end: range.rangeEnd,
  });
  if (locationId) q.set("location_id", locationId);
  return q.toString();
}

/** Idempotent. The page calls this before reading, on every range change. */
export function deriveTimeEntries(
  range: PayrollRange,
  locationId?: string
): Promise<PayrollDeriveResult> {
  return apiFetch<PayrollDeriveResult>("/payroll/entries/derive", {
    method: "POST",
    body: JSON.stringify(rangeBody(range, locationId)),
  });
}

export function listTimeEntries(
  range: PayrollRange,
  locationId?: string,
  approved?: boolean
): Promise<PayrollEntriesResponse> {
  let q = rangeQuery(range, locationId);
  if (approved !== undefined) q += `&approved=${approved}`;
  return apiFetch<PayrollEntriesResponse>(`/payroll/entries?${q}`);
}

export function listPayrollExceptions(
  range: PayrollRange,
  locationId?: string
): Promise<PayrollExceptionsResponse> {
  return apiFetch<PayrollExceptionsResponse>(
    `/payroll/exceptions?${rangeQuery(range, locationId)}`
  );
}

export function attestShift(
  shiftId: string,
  reason?: string
): Promise<TimeEntryRow> {
  return apiFetch<TimeEntryRow>("/payroll/attest", {
    method: "POST",
    body: JSON.stringify({ shift_id: shiftId, reason: reason || null }),
  });
}

export function approveTimeEntries(
  range: PayrollRange,
  locationId?: string,
  entryIds?: string[]
): Promise<PayrollApproveResult> {
  return apiFetch<PayrollApproveResult>("/payroll/approve", {
    method: "POST",
    body: JSON.stringify({
      ...rangeBody(range, locationId),
      entry_ids: entryIds && entryIds.length > 0 ? entryIds : null,
    }),
  });
}

/** The one call that cannot go through apiFetch, which ends in res.json().
 *
 *  Uses fetch directly with the same Authorization: Bearer header, reads
 *  res.blob(), parses the filename out of Content-Disposition, and throws
 *  ApiError on a non-OK status so the page's error handling is unchanged.
 *  This is why export is a POST — it keeps the credential in a header instead
 *  of a query string. */
export async function downloadPayrollCsv(
  range: PayrollRange,
  locationId?: string,
  includeExported = false
): Promise<{ blob: Blob; filename: string }> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch("/api/v1/payroll/export", {
    method: "POST",
    headers,
    body: JSON.stringify({
      ...rangeBody(range, locationId),
      include_exported: includeExported,
    }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message || res.statusText;
    throw new ApiError(res.status, message, detail);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  return {
    blob: await res.blob(),
    filename: match ? match[1] : "payroll.csv",
  };
}
