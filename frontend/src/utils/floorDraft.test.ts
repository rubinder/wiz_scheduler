import { describe, expect, it } from "vitest";
import type { FloorIn, FloorOut, TableIO } from "../types/reservations";
import { applyDraft, downscaleSize, draftErrorKey, DRAFT_LIMITS, validateDraftInput } from "./floorDraft";
import { editorToFloorIn, floorToEditor } from "./reservations";

const table = (label: string, p: Partial<TableIO> = {}): TableIO => ({
  id: null,
  label,
  section_key: null,
  min_seats: 1,
  max_seats: 2,
  combinable: false,
  x: 0,
  y: 0,
  w: 2,
  h: 2,
  rotation: 0,
  ...p,
});

const saved: FloorOut = {
  sections: [{ id: "sec1", key: "sec1", name: "Main Room", sort_order: 0 }],
  tables: [
    table("T1", { id: "tbl1", section_key: "sec1", combinable: true }),
    table("T2", { id: "tbl2", combinable: true }),
  ],
  combinations: [{ id: "cmb1", name: "T1+T2", table_labels: ["T1", "T2"], min_seats: 2, max_seats: 4 }],
};

const draft: FloorIn = {
  sections: [
    { id: null, key: "main", name: "main room", sort_order: 0 },
    { id: null, key: "patio", name: "Patio", sort_order: 1 },
  ],
  tables: [
    table("T1", { section_key: "main", min_seats: 2, max_seats: 4, combinable: true, x: 5, y: 6 }),
    table("P1", { section_key: "patio", max_seats: 6, combinable: true, x: 10 }),
  ],
  combinations: [
    { id: null, name: "T1+T2", table_labels: ["T1", "P1"] },
    { id: null, name: "Patio pair", table_labels: ["P1", "T1"] },
  ],
};

describe("applyDraft", () => {
  it("replaces the editor floor with the draft's tables and positions", () => {
    const ed = applyDraft(floorToEditor(saved), draft, "ai:1:");
    expect(ed.tables.map((t) => t.label)).toEqual(["T1", "P1"]);
    expect(ed.tables[0]).toMatchObject({ min_seats: 2, max_seats: 4, x: 5, y: 6 });
  });

  it("keeps the ids of tables, sections and combinations it matches, so saving updates them", () => {
    const ed = applyDraft(floorToEditor(saved), draft, "ai:1:");
    const out = editorToFloorIn(ed);
    // T1 keeps its id (its bookings stay); T2 is gone and P1 is new.
    expect(out.tables.map((t) => t.id)).toEqual(["tbl1", null]);
    // "main room" matches "Main Room" regardless of case; Patio is new.
    expect(out.sections.map((s) => s.id)).toEqual(["sec1", null]);
    expect(out.tables[0].section_key).toBe("sec1");
    expect(out.tables[1].section_key).toBe(out.sections[1].key);
    expect(out.combinations.map((c) => c.id)).toEqual(["cmb1", null]);
    expect(out.combinations[1].table_labels).toEqual(["P1", "T1"]);
  });

  it("gives new tables and sections keys that do not collide", () => {
    const ed = applyDraft(floorToEditor(saved), draft, "ai:1:");
    const uids = ed.tables.map((t) => t.uid);
    expect(new Set(uids).size).toBe(uids.length);
    expect(uids[0]).toBe("tbl1");
    expect(uids[1].startsWith("ai:1:")).toBe(true);
    const keys = ed.sections.map((s) => s.key);
    expect(new Set(keys).size).toBe(keys.length);
    // Combination seat ranges are recomputed by the server on save.
    expect(ed.combinations.every((c) => c.seats === null)).toBe(true);
  });

  it("drafts onto an empty floor", () => {
    const ed = applyDraft({ sections: [], tables: [], combinations: [] }, draft, "ai:2:");
    expect(editorToFloorIn(ed).tables.every((t) => t.id === null)).toBe(true);
    expect(ed.combinations).toHaveLength(2);
  });
});

const img = (size = 1000, type = "image/jpeg") => ({ size, type });

describe("validateDraftInput", () => {
  it("accepts photos and PDFs with captions and notes", () => {
    expect(validateDraftInput([img(), img(2000, "application/pdf")], ["front", ""], "notes")).toBeNull();
  });

  it("needs at least one file and at most ten", () => {
    expect(validateDraftInput([], [], "")).toBe("noFiles");
    expect(validateDraftInput(Array.from({ length: 11 }, () => img()), [], "")).toBe("tooManyFiles");
  });

  it("refuses other file types", () => {
    expect(validateDraftInput([img(10, "image/gif")], [""], "")).toBe("unsupportedFile");
  });

  it("applies the image, PDF and total size limits", () => {
    expect(validateDraftInput([img(DRAFT_LIMITS.imageBytes + 1)], [""], "")).toBe("fileTooLarge");
    expect(validateDraftInput([img(DRAFT_LIMITS.imageBytes + 1, "application/pdf")], [""], "")).toBeNull();
    expect(validateDraftInput([img(DRAFT_LIMITS.pdfBytes + 1, "application/pdf")], [""], "")).toBe("fileTooLarge");
    const big = img(DRAFT_LIMITS.pdfBytes, "application/pdf");
    expect(validateDraftInput([big, big], ["", ""], "")).toBe("totalTooLarge");
  });

  it("limits captions and notes", () => {
    expect(validateDraftInput([img()], ["x".repeat(301)], "")).toBe("captionTooLong");
    expect(validateDraftInput([img()], [""], "x".repeat(2001))).toBe("notesTooLong");
  });
});

describe("draftErrorKey", () => {
  it("maps server codes to the modal's messages", () => {
    expect(draftErrorKey("file_too_large")).toBe("fileTooLarge");
    expect(draftErrorKey("ai_credits_required")).toBe("ai_credits_required");
    expect(draftErrorKey("draft_in_progress")).toBe("draft_in_progress");
    expect(draftErrorKey("timeout")).toBe("timeout");
    for (const code of ["refused", "truncated", "unreadable", "model_error", "internal"]) {
      expect(draftErrorKey(code)).toBe("failed");
    }
    expect(draftErrorKey("something_new")).toBe("generic");
    expect(draftErrorKey(null)).toBe("generic");
  });
});

describe("downscaleSize", () => {
  it("shrinks the long edge to 1568 px, keeping the aspect ratio", () => {
    expect(downscaleSize(4032, 3024)).toEqual({ width: 1568, height: 1176 });
    expect(downscaleSize(3024, 4032)).toEqual({ width: 1176, height: 1568 });
  });

  it("leaves a photo that is already small enough alone", () => {
    expect(downscaleSize(1568, 1000)).toBeNull();
    expect(downscaleSize(800, 600)).toBeNull();
  });

  it("never rounds a side down to zero", () => {
    expect(downscaleSize(100000, 10)).toEqual({ width: 1568, height: 1 });
  });

  it("takes another long edge", () => {
    expect(downscaleSize(2000, 1000, 1000)).toEqual({ width: 1000, height: 500 });
  });
});
