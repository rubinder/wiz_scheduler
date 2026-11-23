/**
 * The AI host billing page's "Call capacity" card (decision 103): the
 * request form and the company staff line. The server decides everything
 * else (allocation, peaks); this only checks what the admin typed.
 */

export interface CapacityForm {
  locationIds: string[];
  expectedPeak: string;
  /** YYYY-MM-DD from a date input, or "" (never parsed into a Date). */
  neededBy: string;
  note: string;
}

export type CapacityProblem = "locations" | "peak" | "note";

export const NOTE_MAX = 1000;

export const emptyCapacityForm = (locationIds: string[] = []): CapacityForm => ({
  locationIds: [...locationIds],
  expectedPeak: "",
  neededBy: "",
  note: "",
});

export function capacityRequestProblem(form: CapacityForm, maxPeak: number): CapacityProblem | null {
  if (form.locationIds.length === 0) return "locations";
  const peak = form.expectedPeak.trim();
  if (!/^\d+$/.test(peak) || Number(peak) < 1 || Number(peak) > maxPeak) return "peak";
  if (form.note.trim().length > NOTE_MAX) return "note";
  return null;
}

export interface CapacityRequestBody {
  location_ids: string[];
  expected_peak: number;
  needed_by: string | null;
  note: string;
}

export const capacityRequestBody = (form: CapacityForm): CapacityRequestBody => ({
  location_ids: form.locationIds,
  expected_peak: Number(form.expectedPeak.trim()),
  needed_by: form.neededBy || null,
  note: form.note.trim(),
});

const E164 = /^\+[1-9]\d{6,14}$/;

/** True when a non-blank number is not E.164 (blank clears the staff line). */
export const transferNumberProblem = (value: string): boolean =>
  value.trim() !== "" && !E164.test(value.trim());

export const transferNumberBody = (value: string): { default_staff_transfer_number: string | null } => ({
  default_staff_transfer_number: value.trim() === "" ? null : value.trim(),
});
