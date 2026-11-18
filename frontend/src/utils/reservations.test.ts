import { describe, expect, it } from "vitest";
import type { RulesSchema } from "../types/reservations";
import { errorKey, formatLocalTime, localDateString, payloadToRulesForm, rulesFormToPayload } from "./reservations";

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

describe("rules form conversion", () => {
  const rules: RulesSchema = {
    service_periods: [{ name: "Dinner", weekdays: [4, 5], first_seating: "17:00", last_seating: "21:30" }],
    slot_interval_minutes: 15,
    durations: [{ max_party: 4, minutes: 90, churn_minutes: 10 }],
    max_concurrent_covers: 40,
    max_covers_per_slot: null,
    date_overrides: [{ date: "2026-12-24", periods: [], note: "Eve" }],
    max_self_book_party: 6,
    max_party_size: 12,
    booking_horizon_days: 60,
    cutoff_minutes: 30,
    closures: ["2026-12-25"],
    section_cover_limits: { patio: 20 },
    hold_timeout_minutes: 10,
    pending_hold_hours: 24,
    persona: { voice_id: "v1", greeting: "Hi", facts: ["We have parking"] },
    staff_transfer_number: null,
  };

  it("round-trips a rules object", () => {
    expect(rulesFormToPayload(payloadToRulesForm(rules))).toEqual(rules);
  });

  it("turns blank capacity, pacing and transfer number into null", () => {
    const form = payloadToRulesForm(rules);
    form.max_concurrent_covers = "";
    form.max_covers_per_slot = "  ";
    form.staff_transfer_number = "";
    const out = rulesFormToPayload(form);
    expect(out.max_concurrent_covers).toBeNull();
    expect(out.max_covers_per_slot).toBeNull();
    expect(out.staff_transfer_number).toBeNull();
  });

  it("shows null as blank and trims seconds from times", () => {
    const form = payloadToRulesForm({
      ...rules,
      service_periods: [{ ...rules.service_periods[0], first_seating: "17:00:00" }],
    });
    expect(form.max_covers_per_slot).toBe("");
    expect(form.service_periods[0].first_seating).toBe("17:00");
  });

  it("reads whitespace-padded numeric strings as numbers", () => {
    const form = payloadToRulesForm(rules);
    form.slot_interval_minutes = " 15 ";
    form.durations[0].minutes = " 90";
    form.max_concurrent_covers = " 40 ";
    const out = rulesFormToPayload(form);
    expect(out.slot_interval_minutes).toBe(15);
    expect(out.durations[0].minutes).toBe(90);
    expect(out.max_concurrent_covers).toBe(40);
  });
});
