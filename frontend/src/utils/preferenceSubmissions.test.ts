import { describe, expect, it } from "vitest";
import type {
  MySubmissionState,
  PreferenceSubmissionDetail,
  PreferenceSubmissionPayload,
  ReviewCurrentView,
} from "../types";
import {
  DEFAULT_WEIGHT,
  MAX_DAY_PREFERENCES,
  buildApprovePayload,
  buildReviewDraft,
  buildSubmitPayload,
  canAddDayPreference,
  draftFromState,
  emptyPayload,
  nextFreeDay,
  prefillLevel,
  prefillWeight,
  submissionProblems,
} from "./preferenceSubmissions";

const emptyCurrent = (): ReviewCurrentView => ({
  max_hours_per_week: null,
  affinities: [],
  blackouts: [],
  day_preferences: [],
  hour_range_preferences: [],
  caps: [],
});

const state = (over: Partial<MySubmissionState> = {}): MySubmissionState => ({
  enabled: true,
  current: {
    max_hours_per_week: 30,
    affinities: [
      {
        target_employee_id: "bob",
        target_name: "Bob",
        relationship: "prefer_apart",
        entry_date: "2026-01-01",
        expiration_date: null,
      },
    ],
    blackouts: [{ day_of_week: 1, start_time: "08:00", end_time: "12:00" }],
    day_preferences: [{ day_of_week: 2 }],
    hour_range_preferences: [{ start_time: "09:00", end_time: "17:00" }],
    caps: [{ start_time: "22:00", end_time: "06:00", max_per_week: 2 }],
  },
  pending: null,
  last_decision: null,
  ...over,
});

const payload = (over: Partial<PreferenceSubmissionPayload> = {}): PreferenceSubmissionPayload => ({
  ...emptyPayload(),
  ...over,
});

describe("draftFromState", () => {
  it("starts from the pending payload when there is one", () => {
    const pendingPayload = payload({
      max_hours_per_week: 12,
      day_preferences: [{ day_of_week: 5, employee_importance: "high" }],
    });
    const draft = draftFromState(
      state({ pending: { id: "p1", submitted_at: "2026-10-27T00:00:00Z", payload: pendingPayload } }),
    );
    expect(draft).toEqual(pendingPayload);
    expect(draft).not.toBe(pendingPayload);
    draft.day_preferences.push({ day_of_week: 6, employee_importance: "low" });
    expect(pendingPayload.day_preferences).toHaveLength(1);
  });

  it("otherwise copies the current values with medium importance", () => {
    expect(draftFromState(state())).toEqual({
      max_hours_per_week: 30,
      affinities: [
        {
          target_employee_id: "bob",
          relationship: "prefer_apart",
          entry_date: "2026-01-01",
          expiration_date: null,
        },
      ],
      blackouts: [{ day_of_week: 1, start_time: "08:00", end_time: "12:00" }],
      day_preferences: [{ day_of_week: 2, employee_importance: "medium" }],
      hour_range_preferences: [
        { start_time: "09:00", end_time: "17:00", employee_importance: "medium" },
      ],
      caps: [
        { start_time: "22:00", end_time: "06:00", max_per_week: 2, employee_importance: "medium" },
      ],
    });
  });

  it("drops associations with people who are no longer coworkers", () => {
    const draft = draftFromState(state(), [{ id: "carol", full_name: "Carol" }]);
    expect(draft.affinities).toEqual([]);
    const kept = draftFromState(state(), [{ id: "bob", full_name: "Bob" }]);
    expect(kept.affinities).toHaveLength(1);
  });

  it("never carries a weight or level into the employee draft", () => {
    const json = JSON.stringify(draftFromState(state()));
    expect(json).not.toContain("weight");
    expect(json).not.toContain("level");
  });
});

describe("day preference cap", () => {
  it("allows adding until three days are chosen", () => {
    expect(MAX_DAY_PREFERENCES).toBe(3);
    const days = (n: number) =>
      payload({
        day_preferences: Array.from({ length: n }, (_, i) => ({
          day_of_week: i,
          employee_importance: "medium" as const,
        })),
      });
    expect(canAddDayPreference(days(0))).toBe(true);
    expect(canAddDayPreference(days(2))).toBe(true);
    expect(canAddDayPreference(days(3))).toBe(false);
  });

  it("picks the first day not already chosen", () => {
    expect(nextFreeDay(payload())).toBe(0);
    expect(
      nextFreeDay(
        payload({
          day_preferences: [
            { day_of_week: 0, employee_importance: "low" },
            { day_of_week: 1, employee_importance: "low" },
          ],
        }),
      ),
    ).toBe(2);
  });

  it("reports more than three days as a problem", () => {
    const draft = payload({
      day_preferences: [0, 1, 2, 3].map((d) => ({ day_of_week: d, employee_importance: "low" as const })),
    });
    expect(submissionProblems(draft)).toContain("tooManyDays");
  });
});

describe("submissionProblems", () => {
  it("is empty for a valid draft", () => {
    expect(submissionProblems(draftFromState(state()))).toEqual([]);
  });

  it("flags a blackout whose end is not after its start", () => {
    const draft = payload({ blackouts: [{ day_of_week: 0, start_time: "12:00", end_time: "12:00" }] });
    expect(submissionProblems(draft)).toContain("blackoutOrder");
    const overnight = payload({ blackouts: [{ day_of_week: 0, start_time: "22:00", end_time: "02:00" }] });
    expect(submissionProblems(overnight)).toContain("blackoutOrder");
  });

  it("allows overnight hour ranges and caps but not zero-length ones", () => {
    expect(
      submissionProblems(
        payload({
          hour_range_preferences: [{ start_time: "22:00", end_time: "06:00", employee_importance: "low" }],
        }),
      ),
    ).toEqual([]);
    expect(
      submissionProblems(
        payload({
          caps: [{ start_time: "09:00", end_time: "09:00", max_per_week: 1, employee_importance: "low" }],
        }),
      ),
    ).toContain("zeroRange");
  });

  it("flags duplicates and a coworker row with nobody chosen", () => {
    const draft = payload({
      day_preferences: [
        { day_of_week: 1, employee_importance: "low" },
        { day_of_week: 1, employee_importance: "high" },
      ],
      hour_range_preferences: [
        { start_time: "09:00", end_time: "17:00", employee_importance: "low" },
        { start_time: "09:00", end_time: "17:00", employee_importance: "high" },
      ],
      affinities: [
        { target_employee_id: "a", relationship: "prefer_with" },
        { target_employee_id: "a", relationship: "prefer_apart" },
        { target_employee_id: "", relationship: "prefer_with" },
      ],
    });
    const problems = submissionProblems(draft);
    expect(problems).toContain("duplicateDay");
    expect(problems).toContain("duplicateRange");
    expect(problems).toContain("duplicateCoworker");
    expect(problems).toContain("missingCoworker");
  });
});

describe("buildSubmitPayload", () => {
  it("keeps only the employee fields", () => {
    const draft = draftFromState(state());
    const built = buildSubmitPayload(draft);
    expect(built).toEqual(draft);
    for (const row of built.day_preferences) {
      expect(Object.keys(row).sort()).toEqual(["day_of_week", "employee_importance"]);
    }
  });

  it("sends a blank max-hours value as null", () => {
    expect(buildSubmitPayload(payload({ max_hours_per_week: Number.NaN })).max_hours_per_week).toBeNull();
  });
});

describe("prefillWeight", () => {
  const current: ReviewCurrentView = {
    ...emptyCurrent(),
    day_preferences: [{ day_of_week: 2, weight: 0.4 }],
    hour_range_preferences: [{ start_time: "09:00", end_time: "17:00", weight: 0.9 }],
    caps: [{ start_time: "22:00", end_time: "06:00", max_per_week: 2, weight: 0.2 }],
  };

  it("uses the matching live row's weight", () => {
    expect(prefillWeight.day(2, current)).toBe(0.4);
    expect(prefillWeight.hourRange("09:00", "17:00", current)).toBe(0.9);
    expect(prefillWeight.cap("22:00", "06:00", current)).toBe(0.2);
  });

  it("falls back to 0.7 when nothing matches", () => {
    expect(DEFAULT_WEIGHT).toBe(0.7);
    expect(prefillWeight.day(3, current)).toBe(0.7);
    expect(prefillWeight.hourRange("09:00", "18:00", current)).toBe(0.7);
    expect(prefillWeight.cap("09:00", "17:00", current)).toBe(0.7);
  });
});

describe("prefillLevel", () => {
  const current: ReviewCurrentView = {
    ...emptyCurrent(),
    affinities: [
      {
        target_employee_id: "bob",
        target_name: "Bob",
        relationship: "prefer_with",
        entry_date: null,
        expiration_date: null,
        level: 1,
      },
    ],
  };

  it("uses the live level for the same coworker", () => {
    expect(prefillLevel({ target_employee_id: "bob", relationship: "prefer_apart" }, current)).toBe(1);
  });

  it("maps the relationship otherwise", () => {
    expect(prefillLevel({ target_employee_id: "x", relationship: "prefer_with" }, current)).toBe(0.5);
    expect(prefillLevel({ target_employee_id: "x", relationship: "prefer_apart" }, current)).toBe(-0.5);
    expect(prefillLevel({ target_employee_id: "x", relationship: "never_together" }, current)).toBe(-1);
  });
});

describe("review draft and approve payload", () => {
  const detail = (): PreferenceSubmissionDetail => ({
    id: "s1",
    status: "pending",
    employee: { id: "ann", full_name: "Ann", location_ids: ["l1"] },
    submitted_at: "2026-10-27T00:00:00Z",
    reviewed_at: null,
    reviewed_by: null,
    denial_reason: null,
    approved_payload: null,
    current: {
      ...emptyCurrent(),
      day_preferences: [{ day_of_week: 2, weight: 0.4 }],
      affinities: [
        {
          target_employee_id: "bob",
          target_name: "Bob",
          relationship: "prefer_apart",
          entry_date: "2026-01-01",
          expiration_date: null,
          level: -0.5,
        },
      ],
    },
    submitted_payload: {
      max_hours_per_week: 25,
      affinities: [
        { target_employee_id: "bob", relationship: "never_together", entry_date: "2026-01-01", expiration_date: null },
        { target_employee_id: "cat", relationship: "prefer_with", entry_date: null, expiration_date: null },
      ],
      blackouts: [{ day_of_week: 0, start_time: "08:00", end_time: "10:00" }],
      day_preferences: [
        { day_of_week: 2, employee_importance: "high" },
        { day_of_week: 4, employee_importance: "low" },
      ],
      hour_range_preferences: [{ start_time: "09:00", end_time: "17:00", employee_importance: "medium" }],
      caps: [{ start_time: "22:00", end_time: "06:00", max_per_week: 1, employee_importance: "high" }],
    },
  });

  it("prefills every weight and level from the submission", () => {
    const draft = buildReviewDraft(detail());
    expect(draft.day_preferences).toEqual([
      { day_of_week: 2, weight: 0.4, employee_importance: "high" },
      { day_of_week: 4, weight: 0.7, employee_importance: "low" },
    ]);
    expect(draft.hour_range_preferences[0].weight).toBe(0.7);
    expect(draft.caps[0].weight).toBe(0.7);
    expect(draft.affinities.map((a) => a.level)).toEqual([-0.5, 0.5]);
    expect(draft.max_hours_per_week).toBe(25);
    expect(draft.blackouts).toEqual(detail().submitted_payload.blackouts);
  });

  it("builds an approve body with an explicit weight and level on every row", () => {
    const draft = buildReviewDraft(detail());
    draft.day_preferences[1].weight = 0.33;
    const body = buildApprovePayload(draft);
    expect(body.day_preferences[1].weight).toBe(0.3);
    for (const r of [...body.day_preferences, ...body.hour_range_preferences, ...body.caps]) {
      expect(typeof r.weight).toBe("number");
      expect(r.weight).toBeGreaterThanOrEqual(0);
      expect(r.weight).toBeLessThanOrEqual(1);
    }
    for (const a of body.affinities) expect([-1, -0.5, 0.5, 1]).toContain(a.level);
    expect(body.caps[0]).toEqual({
      start_time: "22:00",
      end_time: "06:00",
      max_per_week: 1,
      weight: 0.7,
      employee_importance: "high",
    });
  });

  it("clamps out-of-range weights and sends a blank max-hours as null", () => {
    const draft = buildReviewDraft(detail());
    draft.day_preferences[0].weight = 1.4;
    draft.hour_range_preferences[0].weight = -0.2;
    draft.max_hours_per_week = Number.NaN;
    const body = buildApprovePayload(draft);
    expect(body.day_preferences[0].weight).toBe(1);
    expect(body.hour_range_preferences[0].weight).toBe(0);
    expect(body.max_hours_per_week).toBeNull();
  });
});
