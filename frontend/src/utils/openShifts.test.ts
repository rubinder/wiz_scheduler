import { describe, expect, it } from "vitest";
import type { ShiftAssignment } from "../types";
import {
  countOpen,
  groupCandidates,
  isOpenShift,
  missingFlags,
  openSlotTimes,
  toOpenAssignment,
  weekDates,
  type Candidate,
} from "./openShifts";

const shift = (over: Partial<ShiftAssignment> = {}): ShiftAssignment => ({
  employee_id: "e1",
  employee_name: "Ana",
  role_id: "r1",
  role_name: "Cook",
  location_id: "l1",
  date: "2026-08-31",
  start_time: "2026-08-31T09:00:00-04:00",
  end_time: "2026-08-31T17:00:00-04:00",
  status: "ok",
  ...over,
});

const cand = (name: string, tier: 1 | 2 | 3, over: Partial<Candidate> = {}): Candidate => ({
  employee_id: name.toLowerCase(),
  full_name: name,
  tier,
  available: true,
  has_role: true,
  at_location: true,
  ...over,
});

describe("isOpenShift", () => {
  it("is true for a null employee", () => {
    expect(isOpenShift(shift({ employee_id: null }))).toBe(true);
  });
  it("is true for a legacy VACANT draft entry", () => {
    expect(isOpenShift(shift({ employee_id: "VACANT", status: "VACANT" }))).toBe(true);
  });
  it("is true for status OPEN", () => {
    expect(isOpenShift(shift({ employee_id: null, status: "OPEN" }))).toBe(true);
  });
  it("is false for a filled shift", () => {
    expect(isOpenShift(shift())).toBe(false);
  });
  it("is never true for a conflict", () => {
    expect(isOpenShift(shift({ employee_id: "VACANT", status: "CONFLICT" }))).toBe(false);
  });
});

describe("countOpen", () => {
  it("counts only open shifts", () => {
    expect(countOpen([shift(), shift({ employee_id: null }), shift({ employee_id: "VACANT" })])).toBe(2);
  });
});

describe("toOpenAssignment", () => {
  it("keeps the slot and clears the person", () => {
    const out = toOpenAssignment(shift({ preference_violations: [{ kind: "day" } as never] }));
    expect(out).toMatchObject({ employee_id: null, employee_name: null, status: "OPEN", preference_violations: [] });
    expect(out.role_id).toBe("r1");
    expect(out.start_time).toBe("2026-08-31T09:00:00-04:00");
  });
});

describe("groupCandidates", () => {
  const all = [cand("Ana", 1), cand("Ben", 2), cand("Bea", 2), cand("Dan", 3)];
  it("groups by tier in tier order and drops empty tiers", () => {
    expect(groupCandidates(all, "").map((g) => [g.tier, g.candidates.map((c) => c.full_name)])).toEqual([
      [1, ["Ana"]],
      [2, ["Ben", "Bea"]],
      [3, ["Dan"]],
    ]);
  });
  it("filters by name within groups, case-insensitively", () => {
    expect(groupCandidates(all, "be").map((g) => [g.tier, g.candidates.map((c) => c.full_name)])).toEqual([
      [2, ["Ben", "Bea"]],
    ]);
  });
});

describe("missingFlags", () => {
  it("lists what keeps someone out of tier 1", () => {
    expect(missingFlags(cand("X", 2, { available: false }))).toEqual(["availability"]);
    expect(missingFlags(cand("Z", 3, { has_role: false }))).toEqual(["role"]);
    expect(missingFlags(cand("Y", 1))).toEqual([]);
  });
});

describe("openSlotTimes", () => {
  it("sends naive local wall-clock times, never an offset", () => {
    expect(openSlotTimes("2026-08-31", "09:00", "17:00")).toEqual({
      start_time: "2026-08-31T09:00:00",
      end_time: "2026-08-31T17:00:00",
    });
  });

  it("appends no offset on a DST-change Sunday (US fall back)", () => {
    // 2026-11-01: New York moves from -04:00 to -05:00. Borrowing the
    // week's first offset would store this shift an hour off.
    const t = openSlotTimes("2026-11-01", "10:00", "18:00");
    expect(t).toEqual({
      start_time: "2026-11-01T10:00:00",
      end_time: "2026-11-01T18:00:00",
    });
    expect(t.start_time).not.toMatch(/(Z|[+-]\d{2}:\d{2})$/);
    expect(t.end_time).not.toMatch(/(Z|[+-]\d{2}:\d{2})$/);
  });

  it("rolls an end at or before the start to the next day (overnight)", () => {
    expect(openSlotTimes("2026-08-31", "22:00", "02:00")).toEqual({
      start_time: "2026-08-31T22:00:00",
      end_time: "2026-09-01T02:00:00",
    });
    expect(openSlotTimes("2026-12-31", "09:00", "09:00").end_time).toBe(
      "2027-01-01T09:00:00",
    );
  });
});

describe("weekDates", () => {
  it("returns the seven dates from the week start", () => {
    expect(weekDates("2026-10-26")).toEqual([
      "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
      "2026-10-30", "2026-10-31", "2026-11-01",
    ]);
  });

  it("crosses a month and year end", () => {
    expect(weekDates("2026-12-28").slice(-3)).toEqual([
      "2027-01-01", "2027-01-02", "2027-01-03",
    ]);
  });
});
