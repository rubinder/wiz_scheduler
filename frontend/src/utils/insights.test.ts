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
  hasAgentActivity,
  isDefaultRange,
  latestValue,
  PALETTE,
  periodColor,
  pointsDelta,
  ratio,
  sevenDayKpis,
  seriesMean,
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
  const k = sevenDayKpis(days, "2029-12-01", "2030-01-14");

  it("computes ratios from summed counts, not an average of daily ratios", () => {
    if (!k.current || !k.previous) throw new Error("both windows are inside the range");
    // 1/1 and 0/9 per day would average to 50%; summed it is 1/10.
    expect(k.current.resolution).toBe(0.1);
    expect(k.current.conversion).toBe(0.3);
    expect(k.previous.resolution).toBe(0.5);
    expect(k.previous.conversion).toBe(0.25);
    expect(k.current.conversations).toBe(10);
    expect(k.previous.conversations).toBe(20);
  });

  it("is null where a window's denominator is zero", () => {
    if (!k.current || !k.previous) throw new Error("both windows are inside the range");
    expect(k.current.spamBlocked).toBeNull();
    expect(k.previous.spamBlocked).toBe(0.1);
  });

  it("divides summed money by bookings without float drift in the sum", () => {
    if (!k.current || !k.previous) throw new Error("both windows are inside the range");
    expect(k.current.costPerBooking).toBeCloseTo(0.1, 10);
    expect(k.current.billedPerBooking).toBeCloseTo(0.1, 10);
    expect(k.previous.costPerBooking).toBeCloseTo(0.15, 10);
    expect(k.previous.billedPerBooking).toBe(1);
  });

  it("is all null with no rows", () => {
    const empty = sevenDayKpis([], "2029-12-01", "2030-01-14");
    expect(empty.current).toEqual({
      conversations: 0,
      resolution: null,
      conversion: null,
      spamBlocked: null,
      costPerBooking: null,
      billedPerBooking: null,
    });
  });

  it("has no previous window when the range is 10 days: its 7 days were not fetched", () => {
    const ten = sevenDayKpis(days, "2030-01-05", "2030-01-14");
    expect(ten.previous).toBeNull();
    expect(ten.current?.conversations).toBe(10);
  });

  it("has a last window but no previous one when the range is exactly 7 days", () => {
    const seven = sevenDayKpis(days, "2030-01-08", "2030-01-14");
    expect(seven.previous).toBeNull();
    expect(seven.current?.resolution).toBe(0.1);
  });

  it("has neither window when the range is shorter than 7 days", () => {
    expect(sevenDayKpis(days, "2030-01-09", "2030-01-14")).toEqual({ current: null, previous: null });
  });

  it("has both windows from exactly 14 days", () => {
    const both = sevenDayKpis(days, "2030-01-01", "2030-01-14");
    expect(both.previous?.conversations).toBe(20);
  });

  it("shows per-booking money as unknown when an amount does not parse", () => {
    const bad = sevenDayKpis([row("2030-01-14", { booked: 2, cost_usd: "n/a", billed_usd: "1.00" })], "2030-01-01", "2030-01-14");
    expect(bad.current?.costPerBooking).toBeNull();
    expect(bad.current?.billedPerBooking).toBe(0.5);
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

  it("lists every service period seen, sorted, when there is no rules order", () => {
    expect(coverPeriods(days)).toEqual(["dinner", "lunch"]);
  });

  it("follows the rules' period order, then the rest alphabetically", () => {
    const more = [...days, row("2030-01-03", { covers_by_period: { brunch: 1, zz_old: 1 } })];
    expect(coverPeriods(more, ["lunch", "dinner", "breakfast"])).toEqual(["lunch", "dinner", "brunch", "zz_old"]);
  });
});

describe("defaultRange", () => {
  it("is the 30 days ending yesterday (UTC), the newest day the nightly job can have written", () => {
    expect(defaultRange("2030-03-01")).toEqual({ from: "2030-01-30", to: "2030-02-28" });
  });
});

describe("periodColor", () => {
  it("is stable per period name, whatever else is in the range", () => {
    expect(periodColor("dinner")).toBe(periodColor("dinner"));
    expect(PALETTE).toContain(periodColor("dinner"));
    expect(PALETTE).toContain(periodColor("late night"));
  });

  it("uses only colours with at least 3:1 contrast against white", () => {
    const lum = (hex: string) => {
      const [r, g, b] = [1, 3, 5]
        .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    for (const c of PALETTE) expect(1.05 / (lum(c) + 0.05)).toBeGreaterThanOrEqual(3);
  });
});

describe("isDefaultRange", () => {
  it("is true only for the 30 days ending yesterday", () => {
    expect(isDefaultRange("2030-01-30", "2030-02-28", "2030-03-01")).toBe(true);
    expect(isDefaultRange("2030-01-31", "2030-02-28", "2030-03-01")).toBe(false);
  });
});

describe("latestValue", () => {
  it("is the last non-null value of a series, or null", () => {
    expect(latestValue([{ a: 1 }, { a: 2 }, { a: null }], "a")).toBe(2);
    expect(latestValue([{ a: null }], "a")).toBeNull();
  });
});

describe("seriesMean", () => {
  it("averages the non-null values of a series, or null when there are none", () => {
    expect(seriesMean([{ a: 100 }, { a: null }, { a: 300 }], "a")).toBe(200);
    expect(seriesMean([{ a: null }], "a")).toBeNull();
  });
});

describe("hasAgentActivity", () => {
  it("is false with no days", () => {
    expect(hasAgentActivity([])).toBe(false);
  });

  it("is false when the only rows are covers from other booking sources", () => {
    expect(hasAgentActivity([row("2026-11-17", { covers_by_period: { Dinner: 4 } }), row("2026-11-18")])).toBe(false);
  });

  it("is true with a conversation or a screened call on any day", () => {
    expect(hasAgentActivity([row("2026-11-17"), row("2026-11-18", { conversations: 1 })])).toBe(true);
    expect(hasAgentActivity([row("2026-11-17", { screened: 2 })])).toBe(true);
  });
});
