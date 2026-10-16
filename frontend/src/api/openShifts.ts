import { apiFetch } from "./client";
import type { Candidate } from "../utils/openShifts";

/** The slot to rank people for. Times are local "HH:MM" at the location. */
export interface CandidateSlot {
  location_id: string;
  role_id: string;
  date: string;
  start_time: string;
  end_time: string;
  exclude_shift_id?: string | null;
}

export function fetchCandidates(slot: CandidateSlot): Promise<Candidate[]> {
  return apiFetch<Candidate[]>("/schedules/candidates", {
    method: "POST",
    body: JSON.stringify(slot),
  });
}

export async function getOpenShiftCount(): Promise<number> {
  const res = await apiFetch<{ count: number }>("/schedules/open-shifts/count");
  return res.count;
}

/** Fired after anything that can change the number of open approved shifts
 *  (an approved-shift edit, an approval), so the Sidebar badge refetches. */
export const OPEN_SHIFTS_CHANGED = "wiz:open-shifts-changed";

export function notifyOpenShiftsChanged(): void {
  window.dispatchEvent(new Event(OPEN_SHIFTS_CHANGED));
}
