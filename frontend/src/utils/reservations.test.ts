import { describe, expect, it } from "vitest";
import { errorKey, formatLocalTime, localDateString } from "./reservations";

describe("reservations helpers", () => {
  it("formats time in the restaurant zone, not the process zone", () => {
    expect(formatLocalTime("2026-12-04T19:00:00-05:00", "America/New_York", "en")).toContain("7:00");
  });

  it("gives the local date in the restaurant zone", () => {
    expect(localDateString(new Date(Date.UTC(2026, 11, 5, 3, 0)), "America/New_York")).toBe("2026-12-04");
  });

  it("maps error codes to i18n keys", () => {
    expect(errorKey({ code: "table_has_bookings" })).toBe("reservations.errors.table_has_bookings");
    expect(errorKey("boom")).toBe("reservations.errors.generic");
    expect(errorKey({ code: "nope" })).toBe("reservations.errors.generic");
    expect(errorKey(null)).toBe("reservations.errors.generic");
  });
});
