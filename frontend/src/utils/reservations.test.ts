import { describe, expect, it } from "vitest";
import type { FloorOut, ReservationOut, RulesSchema, TableIO } from "../types/reservations";
import {
  CANVAS,
  clampTable,
  editorToFloorIn,
  errorCode,
  errorKey,
  firstFreeSpot,
  floorToEditor,
  formatLocalDateTime,
  formatLocalTime,
  groupBySeating,
  labelsForIds,
  localDateString,
  moveTable,
  nextLabel,
  nextSectionKey,
  payloadToRulesForm,
  pendingApprovals,
  rulesFormToPayload,
  snap,
  tableStates,
  validParty,
} from "./reservations";

describe("reservations helpers", () => {
  it("formats time in the restaurant zone, not the process zone", () => {
    expect(formatLocalTime("2026-12-04T19:00:00-05:00", "America/New_York", "en")).toContain("7:00");
  });

  it("gives the local date in the restaurant zone", () => {
    expect(localDateString(new Date(Date.UTC(2026, 11, 5, 3, 0)), "America/New_York")).toBe("2026-12-04");
  });

  it("formats date and time in the restaurant zone", () => {
    // 23:30 in New York is already the next day in UTC and in Tokyo (the test TZ).
    const out = formatLocalDateTime("2026-12-05T04:30:00Z", "America/New_York", "en");
    expect(out).toContain("Dec 4");
    expect(out).toContain("11:30");
  });

  it("accepts only whole party sizes of at least one", () => {
    expect(validParty("2")).toBe(true);
    expect(validParty("1")).toBe(true);
    expect(validParty("0")).toBe(false);
    expect(validParty("-1")).toBe(false);
    expect(validParty("2.5")).toBe(false);
    expect(validParty("")).toBe(false);
    expect(validParty("  ")).toBe(false);
    expect(validParty("abc")).toBe(false);
  });

  it("maps error codes to i18n keys", () => {
    expect(errorKey({ code: "table_has_bookings" })).toBe("reservations.errors.table_has_bookings");
    expect(errorKey("boom")).toBe("reservations.errors.generic");
    expect(errorKey({ code: "nope" })).toBe("reservations.errors.generic");
    expect(errorKey(null)).toBe("reservations.errors.generic");
  });

  it("extracts a known error code, else null", () => {
    expect(errorCode({ code: "slot_unavailable", message: "x" })).toBe("slot_unavailable");
    expect(errorCode({ code: "invalid_phone" })).toBe("invalid_phone");
    expect(errorCode({ code: "phone_required" })).toBe("phone_required");
    expect(errorCode({ code: "code_exhausted" })).toBe("code_exhausted");
    expect(errorCode({ code: "nope" })).toBeNull();
    expect(errorCode({ code: 3 })).toBeNull();
    expect(errorCode([{ loc: ["body"], msg: "bad" }])).toBeNull();
    expect(errorCode("boom")).toBeNull();
    expect(errorCode(null)).toBeNull();
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

describe("floor plan geometry", () => {
  it("has a 40 x 25 canvas", () => {
    expect(CANVAS).toEqual({ width: 40, height: 25 });
  });

  it("snaps to half units by default", () => {
    expect(snap(1.26)).toBe(1.5);
    expect(snap(1.24)).toBe(1);
    expect(snap(3.3, 1)).toBe(3);
    expect(snap(-0.2)).toBe(0);
  });

  it("keeps the whole table inside the canvas", () => {
    expect(clampTable({ x: 39, y: -1, w: 2, h: 2 })).toEqual({ x: 38, y: 0 });
    expect(clampTable({ x: 5, y: 30, w: 4, h: 2 })).toEqual({ x: 5, y: 23 });
    expect(clampTable({ x: 3, y: 4, w: 2, h: 2 })).toEqual({ x: 3, y: 4 });
  });

  it("clamps a quarter-turned table by its drawn footprint", () => {
    // A 4 x 2 table turned 90 degrees draws 2 wide and 4 tall around its centre.
    expect(clampTable({ x: 0, y: 0, w: 4, h: 2, rotation: 90 })).toEqual({ x: 0, y: 1 });
    expect(clampTable({ x: -5, y: 5, w: 4, h: 2, rotation: 90 })).toEqual({ x: -1, y: 5 });
    expect(clampTable({ x: 0, y: 30, w: 4, h: 2, rotation: 270 })).toEqual({ x: 0, y: 22 });
    expect(clampTable({ x: 0, y: 0, w: 4, h: 2, rotation: 180 })).toEqual({ x: 0, y: 0 });
  });

  it("moves by a snapped delta and clamps", () => {
    expect(moveTable({ x: 1, y: 1, w: 2, h: 2 }, 0.3, 30)).toEqual({ x: 1.5, y: 23 });
    expect(moveTable({ x: 1, y: 1, w: 2, h: 2 }, -5, 0)).toEqual({ x: 0, y: 1 });
  });

  it("finds the first free 2 x 2 spot", () => {
    expect(firstFreeSpot([])).toEqual({ x: 0, y: 0 });
    const t = (x: number, y: number, w = 2, h = 2) => ({ x, y, w, h, rotation: 0 });
    expect(firstFreeSpot([t(0, 0)])).toEqual({ x: 2, y: 0 });
    expect(firstFreeSpot([t(0, 0), t(3, 0)])).toEqual({ x: 5, y: 0 });
    expect(firstFreeSpot([t(0, 0, 40, 2)])).toEqual({ x: 0, y: 2 });
  });

  it("falls back to the origin when the canvas is full", () => {
    expect(firstFreeSpot([{ x: 0, y: 0, w: 40, h: 25, rotation: 0 }])).toEqual({ x: 0, y: 0 });
  });
});

describe("floor plan labels and keys", () => {
  it("gives the first unused T label", () => {
    expect(nextLabel(["T1", "T3"])).toBe("T2");
    expect(nextLabel([])).toBe("T1");
    expect(nextLabel(["T1", "T2", "Bar"])).toBe("T3");
  });

  it("gives the first unused new section key", () => {
    expect(nextSectionKey([])).toBe("new-1");
    expect(nextSectionKey(["abc12345", "new-1"])).toBe("new-2");
  });

  it("maps table ids to labels, keeping unknown ids", () => {
    const tables = [{ id: "a", label: "T1" }, { id: "b", label: "Patio 2" }] as TableIO[];
    expect(labelsForIds(["b", "zz", "a"], tables)).toEqual(["Patio 2", "zz", "T1"]);
  });
});

describe("floor editor conversion", () => {
  const table = (id: string, label: string, combinable = true): TableIO => ({
    id, label, section_key: "s1", min_seats: 2, max_seats: 4, combinable,
    x: 0, y: 0, w: 2, h: 2, rotation: 0,
  });
  const out: FloorOut = {
    sections: [{ id: "s1", key: "s1", name: "Main", sort_order: 0 }],
    tables: [table("a", "T1"), table("b", "T2")],
    combinations: [{ id: "c1", name: "T1+T2", table_labels: ["T1", "T2"], min_seats: 2, max_seats: 8 }],
  };

  it("round-trips a server floor, dropping the computed seat range", () => {
    const ed = floorToEditor(out);
    expect(ed.combinations[0].uids).toEqual(["a", "b"]);
    expect(ed.combinations[0].seats).toEqual({ min: 2, max: 8 });
    expect(ed.combinations[0].key).toBe("c1");
    expect(editorToFloorIn(ed).combinations[0]).not.toHaveProperty("key");
    expect(editorToFloorIn(ed)).toEqual({
      sections: out.sections,
      tables: out.tables,
      combinations: [{ id: "c1", name: "T1+T2", table_labels: ["T1", "T2"] }],
    });
  });

  it("follows a renamed table and renumbers section order", () => {
    const ed = floorToEditor(out);
    ed.tables[0] = { ...ed.tables[0], label: "Window" };
    ed.sections = [{ id: null, key: "new-1", name: "Bar", sort_order: 9 }, ...ed.sections];
    const p = editorToFloorIn(ed);
    expect(p.combinations[0].table_labels).toEqual(["Window", "T2"]);
    expect(p.sections.map((s) => [s.key, s.sort_order])).toEqual([["new-1", 0], ["s1", 1]]);
    expect(p.tables[0]).not.toHaveProperty("uid");
  });

  it("drops members that no longer exist", () => {
    const ed = floorToEditor(out);
    ed.tables = ed.tables.filter((t) => t.uid !== "b");
    expect(editorToFloorIn(ed).combinations[0].table_labels).toEqual(["T1"]);
  });
});

const NY = "America/New_York";

function res(id: string, start: string, status: string, tables: string[] = []): ReservationOut {
  return {
    id,
    location_id: "loc1",
    party_size: 2,
    start,
    end: start,
    status,
    tables,
    diner_name: id,
    diner_phone: null,
    diner_email: null,
    notes: null,
    source: "staff",
    confirmation_code: null,
    held_until: null,
  };
}

describe("pendingApprovals", () => {
  it("lists only submitted requests (with a code), ordered by start", () => {
    const submitted = { ...res("a", "2026-12-04T20:00:00-05:00", "pending_approval"), confirmation_code: "ABC123" };
    const earlier = { ...res("b", "2026-12-04T18:00:00-05:00", "pending_approval"), confirmation_code: "DEF456" };
    const unsubmitted = res("c", "2026-12-04T19:00:00-05:00", "pending_approval");
    const booked = { ...res("d", "2026-12-04T19:00:00-05:00", "booked"), confirmation_code: "GHI789" };
    expect(pendingApprovals([submitted, unsubmitted, booked, earlier]).map((r) => r.id)).toEqual(["b", "a"]);
  });
});

describe("groupBySeating", () => {
  it("is empty for no reservations", () => {
    expect(groupBySeating([], NY)).toEqual([]);
  });

  it("groups by restaurant-time seating, ordered by start (TZ=Asia/Tokyo)", () => {
    const groups = groupBySeating(
      [
        res("a", "2026-12-04T19:00:00-05:00", "booked"),
        res("b", "2026-12-04T18:30:00-05:00", "booked"),
        res("c", "2026-12-05T00:00:00Z", "seated"),
      ],
      NY,
    );
    expect(groups.map((g) => g.time)).toEqual(["18:30", "19:00"]);
    expect(groups[1].items.map((r) => r.id)).toEqual(["a", "c"]);
  });

  it("leaves the agent's short holds off the host list", () => {
    const groups = groupBySeating(
      [res("a", "2026-12-04T19:00:00-05:00", "held"), res("b", "2026-12-04T19:00:00-05:00", "booked")],
      NY,
    );
    expect(groups).toEqual([{ time: "19:00", items: [expect.objectContaining({ id: "b" })] }]);
  });

  it("drops cancelled, declined and expired rows, and leaves pending ones to approvals", () => {
    const groups = groupBySeating(
      [
        res("a", "2026-12-04T19:00:00-05:00", "cancelled"),
        res("b", "2026-12-04T19:00:00-05:00", "declined"),
        res("c", "2026-12-04T19:00:00-05:00", "expired"),
        res("d", "2026-12-04T19:00:00-05:00", "pending_approval"),
        res("e", "2026-12-04T19:30:00-05:00", "completed"),
      ],
      NY,
    );
    expect(groups).toEqual([{ time: "19:30", items: [expect.objectContaining({ id: "e" })] }]);
  });
});

describe("tableStates", () => {
  const now = new Date("2026-12-04T18:00:00-05:00");
  const tables = [{ label: "T1" }, { label: "T2" }, { label: "T3" }, { label: "T4" }];

  it("marks seated, upcoming within 60 minutes, and free", () => {
    const states = tableStates(
      tables,
      [
        res("a", "2026-12-04T17:30:00-05:00", "seated", ["T1"]),
        res("b", "2026-12-04T18:45:00-05:00", "booked", ["T2"]),
        res("c", "2026-12-04T19:30:00-05:00", "booked", ["T3"]),
      ],
      now,
    );
    expect(states).toEqual({ T1: "seated", T2: "upcoming", T3: "free", T4: "free" });
  });

  it("prefers seated over upcoming and ignores other statuses", () => {
    const states = tableStates(
      tables,
      [
        res("a", "2026-12-04T18:15:00-05:00", "booked", ["T1"]),
        res("b", "2026-12-04T17:00:00-05:00", "seated", ["T1"]),
        res("c", "2026-12-04T18:10:00-05:00", "cancelled", ["T2"]),
        res("d", "2026-12-04T18:10:00-05:00", "completed", ["T3"]),
      ],
      now,
    );
    expect(states).toEqual({ T1: "seated", T2: "free", T3: "free", T4: "free" });
  });
});
