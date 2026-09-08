import { describe, expect, it } from "vitest";
import { describeViolations, rosterThinMessage } from "./preferenceText";

const S = {
  prefDay: "prefers {days}",
  prefHourRange: "prefers {start}–{end}",
  prefCap: "already at {n}× this week for {start}–{end}",
  prefUnavoidable: "no one else was free",
  weekdaysShort: "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
};

describe("describeViolations", () => {
  it("names the preferred days in order", () => {
    expect(describeViolations([{ kind: "day", weight: 0.7, unavoidable: false, days: [0, 1, 2] }], S))
      .toEqual(["prefers Mon, Tue, Wed"]);
  });

  it("slices HH:MM from stored times without Date", () => {
    expect(describeViolations(
      [{ kind: "hour_range", weight: 0.5, unavoidable: false, start_time: "16:00:00", end_time: "22:00" }], S,
    )).toEqual(["prefers 16:00–22:00"]);
  });

  it("reports the cap with its allowance", () => {
    expect(describeViolations(
      [{ kind: "cap", weight: 0.8, unavoidable: false, start_time: "16:00", end_time: "22:00", max_per_week: 3 }], S,
    )).toEqual(["already at 3× this week for 16:00–22:00"]);
  });

  it("appends the unavoidable note", () => {
    expect(describeViolations([{ kind: "day", weight: 0.7, unavoidable: true, days: [4] }], S))
      .toEqual(["prefers Fri — no one else was free"]);
  });

  it("returns nothing for an empty or missing list", () => {
    expect(describeViolations([], S)).toEqual([]);
    expect(describeViolations(undefined, S)).toEqual([]);
  });
});

describe("rosterThinMessage", () => {
  it("fills both counts", () => {
    expect(rosterThinMessage(
      { shifts_against_preference: 4, unavoidable: 1, roster_thin: true },
      "{unavoidable} of {total} had no one else free.",
    )).toBe("1 of 4 had no one else free.");
  });
});
