/**
 * Pure logic for employee preference submissions (#189).
 *
 * The employee edits a PreferenceSubmissionPayload: the complete desired
 * state of six sections, with Low/Medium/High importance but never a weight
 * or an affinity level. The manager turns it into an ApprovedPayload by
 * adding an explicit weight per weighted row and a level per association,
 * prefilled here. The server re-validates everything; these checks only
 * catch mistakes before a round trip.
 */
import type {
  ApprovedPayload,
  Coworker,
  CoworkerRelationship,
  MySubmissionState,
  PreferenceSubmissionDetail,
  PreferenceSubmissionPayload,
  ReviewCurrentView,
  SubmittedAffinity,
} from "../types";

/** Employee submissions may name at most this many preferred days. */
export const MAX_DAY_PREFERENCES = 3;
/** Weight used when no live row matches a submitted one. */
export const DEFAULT_WEIGHT = 0.7;
/** The affinity levels the API accepts. */
export const AFFINITY_LEVELS = [-1, -0.5, 0, 0.5, 1] as const;

const RELATIONSHIP_LEVEL: Record<CoworkerRelationship, number> = {
  prefer_with: 0.5,
  prefer_apart: -0.5,
  never_together: -1,
  neutral: 0,
};

/** Mirror of the backend's relationship_from_level: -1 never together,
 * above 0 prefer with, 0 neutral, otherwise prefer apart. */
export function relationshipFromLevel(level: number): CoworkerRelationship {
  if (level === -1) return "never_together";
  if (level > 0) return "prefer_with";
  if (level === 0) return "neutral";
  return "prefer_apart";
}

export function emptyPayload(): PreferenceSubmissionPayload {
  return {
    max_hours_per_week: null,
    affinities: [],
    blackouts: [],
    day_preferences: [],
    hour_range_preferences: [],
    caps: [],
  };
}

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/**
 * The editor's starting point: the pending submission if there is one,
 * else a copy of the approved values (which carry no importance, so every
 * row starts at medium). With `coworkers` given, associations with anyone
 * no longer a coworker are dropped: the server would refuse them and the
 * picker could not show them.
 */
export function draftFromState(
  state: MySubmissionState,
  coworkers?: Coworker[],
): PreferenceSubmissionPayload {
  let draft: PreferenceSubmissionPayload;
  if (state.pending) {
    draft = { ...emptyPayload(), ...clone(state.pending.payload) };
  } else {
    const c = state.current;
    draft = {
      max_hours_per_week: c.max_hours_per_week,
      affinities: c.affinities.map((a) => ({
        target_employee_id: a.target_employee_id,
        relationship: a.relationship,
        entry_date: a.entry_date,
        expiration_date: a.expiration_date,
      })),
      blackouts: c.blackouts.map((b) => ({
        day_of_week: b.day_of_week,
        start_time: b.start_time,
        end_time: b.end_time,
      })),
      day_preferences: c.day_preferences.map((d) => ({
        day_of_week: d.day_of_week,
        employee_importance: "medium",
      })),
      hour_range_preferences: c.hour_range_preferences.map((r) => ({
        start_time: r.start_time,
        end_time: r.end_time,
        employee_importance: "medium",
      })),
      caps: c.caps.map((r) => ({
        start_time: r.start_time,
        end_time: r.end_time,
        max_per_week: r.max_per_week,
        employee_importance: "medium",
      })),
    };
  }
  if (coworkers) {
    const ids = new Set(coworkers.map((c) => c.id));
    draft.affinities = draft.affinities.filter((a) => ids.has(a.target_employee_id));
  }
  return draft;
}

export function canAddDayPreference(draft: PreferenceSubmissionPayload): boolean {
  return draft.day_preferences.length < MAX_DAY_PREFERENCES;
}

/** First weekday (Mon=0) not already a preferred day, or 0 if all are. */
export function nextFreeDay(draft: PreferenceSubmissionPayload): number {
  const used = new Set(draft.day_preferences.map((d) => d.day_of_week));
  for (let d = 0; d < 7; d++) if (!used.has(d)) return d;
  return 0;
}

export type SubmissionProblem =
  | "tooManyDays"
  | "duplicateDay"
  | "duplicateRange"
  | "duplicateCap"
  | "duplicateCoworker"
  | "missingCoworker"
  | "blackoutOrder"
  | "zeroRange";

const hasDuplicates = (keys: string[]) => new Set(keys).size !== keys.length;

/** Problems the server would reject with a 422, as translatable codes. */
export function submissionProblems(draft: PreferenceSubmissionPayload): SubmissionProblem[] {
  const out: SubmissionProblem[] = [];
  if (draft.day_preferences.length > MAX_DAY_PREFERENCES) out.push("tooManyDays");
  if (hasDuplicates(draft.day_preferences.map((d) => String(d.day_of_week)))) out.push("duplicateDay");
  if (hasDuplicates(draft.hour_range_preferences.map((r) => `${r.start_time}-${r.end_time}`))) {
    out.push("duplicateRange");
  }
  if (hasDuplicates(draft.caps.map((r) => `${r.start_time}-${r.end_time}`))) out.push("duplicateCap");
  const targets = draft.affinities.map((a) => a.target_employee_id);
  if (targets.some((t) => !t)) out.push("missingCoworker");
  if (hasDuplicates(targets.filter(Boolean))) out.push("duplicateCoworker");
  // Blackouts never span midnight (same rule as the manager's Day Rules page).
  if (draft.blackouts.some((b) => !(b.end_time > b.start_time))) out.push("blackoutOrder");
  // Hour ranges and caps may run overnight but must not be zero-length.
  if ([...draft.hour_range_preferences, ...draft.caps].some((r) => r.start_time === r.end_time)) {
    out.push("zeroRange");
  }
  return out;
}

const maxHours = (v: number | null | undefined): number | null =>
  v === null || v === undefined || Number.isNaN(v) ? null : v;

/** The PUT /preference-submissions/me body: employee fields only. */
export function buildSubmitPayload(draft: PreferenceSubmissionPayload): PreferenceSubmissionPayload {
  return {
    max_hours_per_week: maxHours(draft.max_hours_per_week),
    affinities: draft.affinities.map((a) => ({
      target_employee_id: a.target_employee_id,
      relationship: a.relationship,
      entry_date: a.entry_date ?? null,
      expiration_date: a.expiration_date ?? null,
    })),
    blackouts: draft.blackouts.map((b) => ({
      day_of_week: b.day_of_week,
      start_time: b.start_time,
      end_time: b.end_time,
    })),
    day_preferences: draft.day_preferences.map((d) => ({
      day_of_week: d.day_of_week,
      employee_importance: d.employee_importance,
    })),
    hour_range_preferences: draft.hour_range_preferences.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      employee_importance: r.employee_importance,
    })),
    caps: draft.caps.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      max_per_week: r.max_per_week,
      employee_importance: r.employee_importance,
    })),
  };
}

/** Weight prefill: the matching live row's weight (same day, or same
 * start/end), else DEFAULT_WEIGHT. */
export const prefillWeight = {
  day(day: number, current: ReviewCurrentView): number {
    return current.day_preferences.find((d) => d.day_of_week === day)?.weight ?? DEFAULT_WEIGHT;
  },
  hourRange(start: string, end: string, current: ReviewCurrentView): number {
    return (
      current.hour_range_preferences.find((r) => r.start_time === start && r.end_time === end)
        ?.weight ?? DEFAULT_WEIGHT
    );
  },
  cap(start: string, end: string, current: ReviewCurrentView): number {
    return (
      current.caps.find((r) => r.start_time === start && r.end_time === end)?.weight ??
      DEFAULT_WEIGHT
    );
  },
};

/** Level prefill: the live level for the same coworker when it already
 * means what the employee asked for, else the relationship's default (with
 * 0.5, apart -0.5, never -1, neutral 0). So a live "prefer with" row the
 * employee now wants "never together" prefills -1, not the old 0.5. */
export function prefillLevel(
  affinity: Pick<SubmittedAffinity, "target_employee_id" | "relationship">,
  current: ReviewCurrentView,
): number {
  const live = current.affinities.find((a) => a.target_employee_id === affinity.target_employee_id);
  if (live && relationshipFromLevel(live.level) === affinity.relationship) return live.level;
  return RELATIONSHIP_LEVEL[affinity.relationship];
}

/** The manager's editable copy of a submission, every weight and level
 * prefilled. */
export function buildReviewDraft(detail: PreferenceSubmissionDetail): ApprovedPayload {
  const s = detail.submitted_payload;
  const cur = detail.current;
  return {
    max_hours_per_week: s.max_hours_per_week ?? null,
    affinities: s.affinities.map((a) => ({
      target_employee_id: a.target_employee_id,
      relationship: a.relationship,
      entry_date: a.entry_date ?? null,
      expiration_date: a.expiration_date ?? null,
      level: prefillLevel(a, cur),
    })),
    blackouts: s.blackouts.map((b) => ({ ...b })),
    day_preferences: s.day_preferences.map((d) => ({
      day_of_week: d.day_of_week,
      weight: prefillWeight.day(d.day_of_week, cur),
      employee_importance: d.employee_importance,
    })),
    hour_range_preferences: s.hour_range_preferences.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      weight: prefillWeight.hourRange(r.start_time, r.end_time, cur),
      employee_importance: r.employee_importance,
    })),
    caps: s.caps.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      max_per_week: r.max_per_week,
      weight: prefillWeight.cap(r.start_time, r.end_time, cur),
      employee_importance: r.employee_importance,
    })),
  };
}

/** True while any weighted row has a cleared (NaN) weight: Approve is
 * blocked rather than silently sending a default. */
export function hasMissingWeight(draft: ApprovedPayload): boolean {
  return [...draft.day_preferences, ...draft.hour_range_preferences, ...draft.caps].some((r) =>
    Number.isNaN(r.weight),
  );
}

/** The 422 code for an association change with a coworker the reviewer
 * cannot edit. The submission is still pending, so it stays in the queue. */
export const AFFINITY_OUT_OF_SCOPE_CODE = "affinity_coworker_out_of_scope";

/** The coworker id from an affinity-out-of-scope refusal, else null. */
export function outOfScopeCoworkerId(status: number, data: unknown): string | null {
  if (status !== 422 || typeof data !== "object" || data === null) return null;
  const d = data as { code?: unknown; target_employee_id?: unknown };
  return d.code === AFFINITY_OUT_OF_SCOPE_CODE && typeof d.target_employee_id === "string"
    ? d.target_employee_id
    : null;
}

/** The API takes weights 0..1 in steps of 0.1. */
export function normalizeWeight(w: number): number {
  if (Number.isNaN(w)) return DEFAULT_WEIGHT;
  return Math.round(Math.min(1, Math.max(0, w)) * 10) / 10;
}

/** The POST /preference-submissions/{id}/approve body. */
export function buildApprovePayload(draft: ApprovedPayload): ApprovedPayload {
  return {
    max_hours_per_week: maxHours(draft.max_hours_per_week),
    affinities: draft.affinities.map((a) => ({
      target_employee_id: a.target_employee_id,
      relationship: a.relationship,
      entry_date: a.entry_date ?? null,
      expiration_date: a.expiration_date ?? null,
      level: a.level,
    })),
    blackouts: draft.blackouts.map((b) => ({
      day_of_week: b.day_of_week,
      start_time: b.start_time,
      end_time: b.end_time,
    })),
    day_preferences: draft.day_preferences.map((d) => ({
      day_of_week: d.day_of_week,
      weight: normalizeWeight(d.weight),
      employee_importance: d.employee_importance ?? null,
    })),
    hour_range_preferences: draft.hour_range_preferences.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      weight: normalizeWeight(r.weight),
      employee_importance: r.employee_importance ?? null,
    })),
    caps: draft.caps.map((r) => ({
      start_time: r.start_time,
      end_time: r.end_time,
      max_per_week: r.max_per_week,
      weight: normalizeWeight(r.weight),
      employee_importance: r.employee_importance ?? null,
    })),
  };
}
