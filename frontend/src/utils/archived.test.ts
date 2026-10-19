import { describe, it, expect } from "vitest";
import { archivedHeldIds, choiceRows, isArchived, visibleRows, withArchivedMark } from "./archived";

const rows = [
  { id: "a", archived_at: null },
  { id: "b", archived_at: "2026-10-01T00:00:00Z" },
  { id: "c" },
];

describe("archived rows", () => {
  it("hides archived rows unless asked", () => {
    expect(visibleRows(rows, false).map((r) => r.id)).toEqual(["a", "c"]);
    expect(visibleRows(rows, true).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
  it("treats a missing archived_at as active", () => {
    expect(isArchived(rows[2])).toBe(false);
    expect(isArchived(rows[1])).toBe(true);
  });
  it("finds held ids that point at archived rows, ignoring unknown ids", () => {
    const refs = [
      { id: "x", archived_at: null },
      { id: "y", archived_at: "2026-10-01T00:00:00Z" },
    ];
    expect(archivedHeldIds(["x", "y", "gone"], refs)).toEqual(["y"]);
    expect(archivedHeldIds([], refs)).toEqual([]);
  });
  it("marks archived names for history lookups", () => {
    expect(withArchivedMark("B", rows[1], "Archived")).toBe("B (Archived)");
    expect(withArchivedMark("A", rows[0], "Archived")).toBe("A");
    expect(withArchivedMark("?", undefined, "Archived")).toBe("?");
  });
  it("offers active rows, plus the held row first when it is archived", () => {
    expect(choiceRows(rows, "b").map((r) => r.id)).toEqual(["b", "a", "c"]);
    expect(choiceRows(rows, "a").map((r) => r.id)).toEqual(["a", "c"]);
    expect(choiceRows(rows, null).map((r) => r.id)).toEqual(["a", "c"]);
  });
});
