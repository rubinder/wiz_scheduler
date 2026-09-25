import { describe, expect, it } from "vitest";

import { formatLateness, formatPaidHours } from "./payrollHours";

// The same three strings the payroll block ships, so the test reads like the
// page does.
const LABELS = {
  early: "{minutes} min early",
  late: "{minutes} min late",
  onTime: "On time",
};

describe("formatPaidHours", () => {
  it("renders a full shift as hours to two places", () => {
    expect(formatPaidHours(480)).toBe("8.00");
  });

  it("renders a half hour as .50, not .5", () => {
    expect(formatPaidHours(450)).toBe("7.50");
  });

  it("renders zero without collapsing to an empty string", () => {
    expect(formatPaidHours(0)).toBe("0.00");
  });
});

describe("formatLateness", () => {
  it("renders nothing at all when no arrival was observed", () => {
    // Not "0": an attested entry has no scan, and reporting an on-time
    // arrival nobody saw would put a fact in front of a manager that is not
    // true.
    expect(formatLateness(null, LABELS)).toBe("");
  });

  it("renders on time for exactly zero", () => {
    expect(formatLateness(0, LABELS)).toBe("On time");
  });

  it("renders a negative number through the early branch, unsigned", () => {
    expect(formatLateness(-4, LABELS)).toBe("4 min early");
  });

  it("renders a positive number through the late branch", () => {
    expect(formatLateness(7, LABELS)).toBe("7 min late");
  });
});
