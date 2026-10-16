import type { ShiftAssignment } from "../types";

/** Open shifts (#174): a shift with no one on it yet.
 *
 *  An open shift has `employee_id === null`. Drafts saved before #174 hold
 *  the legacy `"VACANT"` entry instead, so it is read as open too. A
 *  CONFLICT is an error, never a vacancy. */
export const LEGACY_VACANT = "VACANT";

/** 1 = available + role + location, 2 = role + location, 3 = available +
 *  location. Nobody else is offered (#174). */
export type Tier = 1 | 2 | 3;

/** One row of `POST /schedules/candidates`. The server ranks; the client
 *  never evaluates availability itself. */
export interface Candidate {
  employee_id: string;
  full_name: string;
  tier: Tier;
  available: boolean;
  has_role: boolean;
  at_location: boolean;
}

export function isOpenShift(s: { employee_id: string | null; status?: string }): boolean {
  if (s.status === "CONFLICT") return false;
  if (s.status === "OPEN" || s.status === LEGACY_VACANT) return true;
  return s.employee_id === null || s.employee_id === "" || s.employee_id === LEGACY_VACANT;
}

export function countOpen(shifts: { employee_id: string | null; status?: string }[]): number {
  return shifts.filter(isOpenShift).length;
}

export function toOpenAssignment(s: ShiftAssignment): ShiftAssignment {
  return { ...s, employee_id: null, employee_name: null, status: "OPEN", preference_violations: [] };
}

export interface CandidateGroup {
  tier: Tier;
  candidates: Candidate[];
}

/** Candidates grouped by tier (server order kept within a tier), filtered
 *  by name. Empty tiers are dropped. */
export function groupCandidates(candidates: Candidate[], query: string): CandidateGroup[] {
  const q = query.trim().toLowerCase();
  return ([1, 2, 3] as const)
    .map((tier) => ({
      tier,
      candidates: candidates.filter(
        (c) => c.tier === tier && (!q || c.full_name.toLowerCase().includes(q)),
      ),
    }))
    .filter((g) => g.candidates.length > 0);
}

/** Every offered candidate is at the location, so only these can be missing. */
export type MissingFlag = "availability" | "role";

export function missingFlags(c: Candidate): MissingFlag[] {
  const out: MissingFlag[] = [];
  if (!c.available) out.push("availability");
  if (!c.has_role) out.push("role");
  return out;
}
