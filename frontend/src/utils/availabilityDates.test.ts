import { describe, expect, it } from "vitest";
import { dateParts, overlapsExisting, quickSetDates } from "./availabilityDates";

describe("dateParts", () => {
  it("keeps the calendar day whatever the browser zone", () => {
    // `new Date("2026-11-02").getDate()` is 1 in any zone west of UTC.
    expect(dateParts("2026-11-02")).toEqual({ year: 2026, month: 11, day: 2 });
    expect(dateParts("2026-01-01")).toEqual({ year: 2026, month: 1, day: 1 });
  });
});

describe("quickSetDates", () => {
  it("expands weekdays, weekend and the whole week from any day of the week", () => {
    // 2026-11-04 is a Wednesday; its week runs Mon 11-02 to Sun 11-08.
    expect(quickSetDates("2026-11-04", "weekdays")).toEqual([
      "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
    ]);
    expect(quickSetDates("2026-11-04", "weekend")).toEqual(["2026-11-07", "2026-11-08"]);
    expect(quickSetDates("2026-11-08", "week")).toHaveLength(7);
    expect(quickSetDates("2026-11-08", "week")[0]).toBe("2026-11-02");
    expect(quickSetDates("2026-11-02", "weekdays")[0]).toBe("2026-11-02");
  });

  it("crosses month and year boundaries", () => {
    // 2026-12-31 is a Thursday.
    expect(quickSetDates("2026-12-31", "weekend")).toEqual(["2027-01-02", "2027-01-03"]);
    expect(quickSetDates("2027-01-02", "weekdays")[0]).toBe("2026-12-28");
  });
});

describe("overlapsExisting", () => {
  const existing = [
    { start_time: "2026-11-02T09:00:00Z", end_time: "2026-11-02T13:00:00Z" },
    { start_time: "2026-11-03T22:00:00Z", end_time: "2026-11-04T02:00:00Z" },
  ];

  it("detects an overlapping window on the same date", () => {
    expect(overlapsExisting(existing, "2026-11-02", "12:00", "17:00")).toBe(true);
    expect(overlapsExisting(existing, "2026-11-02", "09:00", "13:00")).toBe(true);
  });

  it("allows adjacent windows and other dates", () => {
    expect(overlapsExisting(existing, "2026-11-02", "13:00", "17:00")).toBe(false);
    expect(overlapsExisting(existing, "2026-11-05", "09:00", "17:00")).toBe(false);
  });

  it("treats a window ending the next day as running to midnight", () => {
    expect(overlapsExisting(existing, "2026-11-03", "23:00", "23:59")).toBe(true);
    expect(overlapsExisting(existing, "2026-11-03", "09:00", "17:00")).toBe(false);
  });
});
