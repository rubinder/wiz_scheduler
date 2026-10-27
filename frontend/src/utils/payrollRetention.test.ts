import { describe, expect, it } from "vitest";

import type { PayrollRetentionInfo } from "../types";
import { formatCalendarDate, retentionWarningText } from "./payrollRetention";

const TEMPLATE =
  "{count} pay records dated before {cutoff} will be permanently deleted on {date} under the 7-year retention policy. Download your full history to keep a copy.";

const INFO: PayrollRetentionInfo = {
  retention_days: 2557,
  cutoff_date: "2019-10-19",
  next_sweep_date: "2026-11-01",
  next_sweep_cutoff_date: "2019-10-31",
  oldest_pay_date: "2019-09-02",
  due_count: 42,
  warn: true,
};

describe("formatCalendarDate", () => {
  it("keeps the calendar day in a zone ahead of UTC", () => {
    // npm test pins TZ=Asia/Tokyo; new Date("2026-11-01") would be UTC
    // midnight and could read as another day elsewhere. The day must not move.
    const d = formatCalendarDate("2026-11-01", "en-US");
    expect(d).toBe("11/1/2026");
  });

  it("returns an unparseable value unchanged", () => {
    expect(formatCalendarDate("soon", "en-US")).toBe("soon");
  });
});

describe("retentionWarningText", () => {
  it("fills the count, the cutoff and the sweep date", () => {
    expect(retentionWarningText(TEMPLATE, INFO, "en-US")).toBe(
      "42 pay records dated before 10/31/2019 will be permanently deleted on 11/1/2026 under the 7-year retention policy. Download your full history to keep a copy."
    );
  });

  it("names the next sweep's cutoff, not today's", () => {
    const text = retentionWarningText("{cutoff}", INFO, "en-US");
    expect(text).toBe("10/31/2019");
  });

  it("is null when the backend says not to warn", () => {
    expect(retentionWarningText(TEMPLATE, { ...INFO, warn: false }, "en-US")).toBeNull();
  });

  it("is null when there is no retention info", () => {
    expect(retentionWarningText(TEMPLATE, null, "en-US")).toBeNull();
  });
});
