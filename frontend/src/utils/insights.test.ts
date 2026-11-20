import { describe, expect, it } from "vitest";
import type { InsightsDay } from "../types/reservations";
import {
  chartRows,
  coverPeriods,
  datesBetween,
  defaultRange,
  fillDays,
  formatPercent,
  formatPoints,
  formatUsdAmount,
  handoffTotals,
  pointsDelta,
  ratio,
  sevenDayKpis,
} from "./insights";

function row(day: string, extra: Partial<InsightsDay> = {}): InsightsDay {
  return {
    day,
    conversations: 0,
    resolved: 0,
    booked: 0,
    handed_off: 0,
    abandoned: 0,
    failed: 0,
    booking_intent: 0,
    handoff_reasons: {},
    llm_p50_ms: null,
    llm_p95_ms: null,
    turn_p50_ms: null,
    turn_p95_ms: null,
    cost_usd: "0.000000",
    billed_usd: "0.00",
    screened: 0,
    blocked: 0,
    covers_by_period: {},
    ...extra,
  };
}

describe("datesBetween", () => {
  it("lists every date inclusive, across month and leap-day boundaries", () => {
    expect(datesBetween("2028-02-27", "2028-03-01")).toEqual([
      "2028-02-27",
      "2028-02-28",
      "2028-02-29",
      "2028-03-01",
    ]);
  });

  it("is empty when from is after to", () => {
    expect(datesBetween("2030-01-02", "2030-01-01")).toEqual([]);
  });
});

describe("fillDays", () => {
  it("puts a null on each day without a row", () => {
    const a = row("2030-01-01", { conversations: 3 });
    const c = row("2030-01-03", { conversations: 5 });
    expect(fillDays([c, a], "2030-01-01", "2030-01-04")).toEqual([
      { day: "2030-01-01", row: a },
      { day: "2030-01-02", row: null },
      { day: "2030-01-03", row: c },
      { day: "2030-01-04", row: null },
    ]);
  });
});

describe("ratio", () => {
  it("is null when the denominator is zero", () => {
    expect(ratio(0, 0)).toBeNull();
    expect(ratio(3, 0)).toBeNull();
  });

  it("divides otherwise", () => {
    expect(ratio(1, 4)).toBe(0.25);
  });
});

describe("formatPercent", () => {
  it("shows a dash for null, never 0% or NaN", () => {
    expect(formatPercent(null, "en")).toBe("—");
  });

  it("formats a ratio as a percentage with at most one decimal", () => {
    expect(formatPercent(0.25, "en")).toBe("25%");
    expect(formatPercent(0.6667, "en")).toBe("66.7%");
    expect(formatPercent(0, "en")).toBe("0%");
  });
});

describe("formatPoints and pointsDelta", () => {
  it("gives the change in percentage points, signed", () => {
    expect(pointsDelta(0.5, 0.25)).toBe(25);
    expect(formatPoints(25, "en")).toBe("+25");
    expect(formatPoints(-3.25, "en")).toBe("-3.3");
    expect(formatPoints(0, "en")).toBe("0");
  });

  it("is null when either side is null", () => {
    expect(pointsDelta(null, 0.5)).toBeNull();
    expect(pointsDelta(0.5, null)).toBeNull();
    expect(formatPoints(null, "en")).toBe("—");
  });
});

describe("formatUsdAmount", () => {
  it("shows two decimals, or a dash for null", () => {
    expect(formatUsdAmount(0.5, "en")).toBe("$0.50");
    expect(formatUsdAmount(null, "en")).toBe("—");
  });
});

describe("sevenDayKpis", () => {
  const days = [
    // previous 7 days: 2030-01-01..07
    row("2030-01-01", { conversations: 10, resolved: 5, booked: 1, booking_intent: 4, screened: 10, blocked: 1, cost_usd: "0.100000", billed_usd: "1.00" }),
    row("2030-01-07", { conversations: 10, resolved: 5, booked: 1, booking_intent: 4, screened: 10, blocked: 1, cost_usd: "0.200000", billed_usd: "1.00" }),
    // last 7 days: 2030-01-08..14
    row("2030-01-08", { conversations: 1, resolved: 1, booked: 1, booking_intent: 1, screened: 0, blocked: 0, cost_usd: "0.100000", billed_usd: "0.10" }),
    row("2030-01-14", { conversations: 9, resolved: 0, booked: 2, booking_intent: 9, screened: 0, blocked: 0, cost_usd: "0.200000", billed_usd: "0.20" }),
    // outside both windows
    row("2029-12-31", { conversations: 100, resolved: 100 }),
  ];
  const k = sevenDayKpis(days, "2030-01-14");

  it("computes ratios from summed counts, not an average of daily ratios", () => {
    // 1/1 and 0/9 per day would average to 50%; summed it is 1/10.
    expect(k.current.resolution).toBe(0.1);
    expect(k.current.conversion).toBe(0.3);
    expect(k.previous.resolution).toBe(0.5);
    expect(k.previous.conversion).toBe(0.25);
    expect(k.current.conversations).toBe(10);
    expect(k.previous.conversations).toBe(20);
  });

  it("is null where a window's denominator is zero", () => {
    expect(k.current.spamBlocked).toBeNull();
    expect(k.previous.spamBlocked).toBe(0.1);
  });

  it("divides summed money by bookings without float drift in the sum", () => {
    expect(k.current.costPerBooking).toBeCloseTo(0.1, 10);
    expect(k.current.billedPerBooking).toBeCloseTo(0.1, 10);
    expect(k.previous.costPerBooking).toBeCloseTo(0.15, 10);
    expect(k.previous.billedPerBooking).toBe(1);
  });

  it("is all null with no rows", () => {
    const empty = sevenDayKpis([], "2030-01-14");
    expect(empty.current).toEqual({
      conversations: 0,
      resolution: null,
      conversion: null,
      spamBlocked: null,
      costPerBooking: null,
      billedPerBooking: null,
    });
  });
});

describe("chartRows", () => {
  it("gives nulls for missing days and zero denominators so charts show gaps", () => {
    const filled = fillDays(
      [row("2030-01-01", { conversations: 4, resolved: 2, booking_intent: 0, llm_p50_ms: 900, covers_by_period: { dinner: 6 } })],
      "2030-01-01",
      "2030-01-02",
    );
    const rows = chartRows(filled, ["dinner", "lunch"]);
    expect(rows[0]).toMatchObject({
      day: "2030-01-01",
      resolution: 0.5,
      conversion: null,
      llmP50: 900,
      llmP95: null,
      costPerBooking: null,
      "covers:dinner": 6,
      "covers:lunch": 0,
    });
    expect(rows[1]).toMatchObject({ day: "2030-01-02", resolution: null, llmP50: null, "covers:dinner": null });
  });
});

describe("handoffTotals and coverPeriods", () => {
  const days = [
    row("2030-01-01", { handoff_reasons: { large_party: 2, other: 1 }, covers_by_period: { lunch: 2 } }),
    row("2030-01-02", { handoff_reasons: { other: 3 }, covers_by_period: { dinner: 5 } }),
  ];

  it("sums handoff reasons over the range, most frequent first", () => {
    expect(handoffTotals(days)).toEqual([
      { reason: "other", count: 4 },
      { reason: "large_party", count: 2 },
    ]);
  });

  it("lists every service period seen, sorted", () => {
    expect(coverPeriods(days)).toEqual(["dinner", "lunch"]);
  });
});

describe("defaultRange", () => {
  it("is the 30 days ending yesterday (UTC), the newest day the nightly job can have written", () => {
    expect(defaultRange("2030-03-01")).toEqual({ from: "2030-01-30", to: "2030-02-28" });
  });
});
