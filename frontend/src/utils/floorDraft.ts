/** AI floor-plan drafts (decision 101): input checks and loading a draft into the editor. */
import type { FloorDraftOut, FloorIn, SectionIO } from "../types/reservations";
import type { EditorCombination, EditorFloor, EditorTable } from "./reservations";

/**
 * The server's limits (backend/reservations/floor_ai.py), checked here only so
 * the modal can say what is wrong before uploading; the server decides.
 */
export const DRAFT_LIMITS = {
  files: 10,
  imageBytes: 5 * 1024 * 1024,
  pdfBytes: 20 * 1024 * 1024,
  totalBytes: 30 * 1024 * 1024,
  captionChars: 300,
  notesChars: 2000,
} as const;

export const DRAFT_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const DRAFT_PDF_TYPE = "application/pdf";
/** The file picker's `accept`. */
export const DRAFT_ACCEPT = [...DRAFT_IMAGE_TYPES, DRAFT_PDF_TYPE].join(",");

export type DraftInputError =
  | "noFiles"
  | "tooManyFiles"
  | "unsupportedFile"
  | "fileTooLarge"
  | "totalTooLarge"
  | "captionTooLong"
  | "notesTooLong";

/** The first problem with the modal's inputs, or null. */
export function validateDraftInput(
  files: { size: number; type: string }[],
  captions: string[],
  notes: string,
): DraftInputError | null {
  if (files.length === 0) return "noFiles";
  if (files.length > DRAFT_LIMITS.files) return "tooManyFiles";
  let total = 0;
  for (const f of files) {
    const pdf = f.type === DRAFT_PDF_TYPE;
    if (!pdf && !(DRAFT_IMAGE_TYPES as readonly string[]).includes(f.type)) return "unsupportedFile";
    if (f.size > (pdf ? DRAFT_LIMITS.pdfBytes : DRAFT_LIMITS.imageBytes)) return "fileTooLarge";
    total += f.size;
  }
  if (total > DRAFT_LIMITS.totalBytes) return "totalTooLarge";
  if (captions.some((c) => c.length > DRAFT_LIMITS.captionChars)) return "captionTooLong";
  if (notes.length > DRAFT_LIMITS.notesChars) return "notesTooLong";
  return null;
}

export type DraftErrorKey =
  | DraftInputError
  | "ai_credits_required"
  | "billing_on_hold"
  | "daily_cost_cap_exceeded"
  | "draft_in_progress"
  | "too_many_captions"
  | "too_many_pages"
  | "too_many_failed_drafts"
  | "estimate_unavailable"
  | "upload_rejected"
  | "timeout"
  | "failed"
  | "generic";

const SERVER_CODES: Record<string, DraftErrorKey> = {
  no_files: "noFiles",
  too_many_files: "tooManyFiles",
  unsupported_file: "unsupportedFile",
  file_too_large: "fileTooLarge",
  caption_too_long: "captionTooLong",
  notes_too_long: "notesTooLong",
  ai_credits_required: "ai_credits_required",
  billing_on_hold: "billing_on_hold",
  daily_cost_cap_exceeded: "daily_cost_cap_exceeded",
  draft_in_progress: "draft_in_progress",
  too_many_captions: "too_many_captions",
  too_many_pages: "too_many_pages",
  too_many_failed_drafts: "too_many_failed_drafts",
  estimate_unavailable: "estimate_unavailable",
  // The API refused a file: at the request (422) or as a failed draft.
  upload_rejected: "upload_rejected",
  timeout: "timeout",
  // The call ran but gave no usable plan; none of these is charged.
  refused: "failed",
  truncated: "failed",
  unreadable: "failed",
  model_error: "failed",
  internal: "failed",
};

/** The modal's message for a server error code (a 4xx detail or a failed draft). */
export function draftErrorKey(code: string | null | undefined): DraftErrorKey {
  return (code && SERVER_CODES[code]) || "generic";
}

/** Claude downsizes images past this long edge anyway, so we send no more. */
export const DOWNSCALE_LONG_EDGE = 1568;
const DOWNSCALE_JPEG_QUALITY = 0.85;

/** The size to scale a photo to, or null when it is already small enough. */
export function downscaleSize(
  width: number,
  height: number,
  longEdge: number = DOWNSCALE_LONG_EDGE,
): { width: number; height: number } | null {
  const long = Math.max(width, height);
  if (long <= longEdge) return null;
  const k = longEdge / long;
  return { width: Math.max(1, Math.round(width * k)), height: Math.max(1, Math.round(height * k)) };
}

/**
 * A photo re-encoded as JPEG with its long edge at DOWNSCALE_LONG_EDGE, so a
 * phone photo uploads small. PDFs, small photos, and anything the browser
 * cannot decode are returned unchanged (the server still checks them), and so
 * is a photo whose re-encoding came out no smaller.
 */
export async function downscaleImage(file: File): Promise<File> {
  if (!(DRAFT_IMAGE_TYPES as readonly string[]).includes(file.type)) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const size = downscaleSize(bitmap.width, bitmap.height);
    if (!size) {
      bitmap.close();
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    // JPEG has no transparency: put a PNG's clear areas on white, not black.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, size.width, size.height);
    ctx.drawImage(bitmap, 0, 0, size.width, size.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", DOWNSCALE_JPEG_QUALITY),
    );
    if (!blob || blob.size >= file.size) return file;
    const name = file.name.replace(/\.[^.]*$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch {
    return file;
  }
}

const norm = (s: string) => s.trim().toLowerCase();

/**
 * The editor floor after loading a draft: the draft's sections, tables and
 * combinations replace the current ones. Nothing is saved; the result is
 * unsaved changes.
 *
 * A draft table keeps a current table's id (and uid), so saving updates that
 * row and its bookings stay attached, only when the label matches AND it is
 * plausibly the same table: it seats at least as many, or it is in a section
 * of the same name. The model labels T1..Tn in reading order, so a label
 * alone would rebind bookings to the wrong physical table. Otherwise the
 * draft table is new and the current one is archived on save, where
 * save_floor refuses to archive a table with upcoming bookings (409).
 * Sections and combinations keep their ids by name.
 */
export function applyDraft(current: EditorFloor, draft: FloorIn, uidPrefix: string): EditorFloor {
  const usedKeys = new Set<string>();
  const keyByDraftKey = new Map<string, string>();
  const sections: SectionIO[] = draft.sections.map((s, i) => {
    const match = current.sections.find((c) => norm(c.name) === norm(s.name) && !usedKeys.has(c.key));
    let key = match?.key;
    if (!key) {
      let n = i + 1;
      while (usedKeys.has(`${uidPrefix}s${n}`) || current.sections.some((c) => c.key === `${uidPrefix}s${n}`)) n += 1;
      key = `${uidPrefix}s${n}`;
    }
    usedKeys.add(key);
    keyByDraftKey.set(s.key, key);
    return { id: match?.id ?? null, key, name: match?.name ?? s.name, sort_order: i };
  });

  const sectionName = (sections: SectionIO[], key: string | null) =>
    key === null ? null : norm(sections.find((x) => x.key === key)?.name ?? "") || null;
  const byLabel = new Map(current.tables.map((t) => [t.label, t]));
  const tables: EditorTable[] = draft.tables.map((t, i) => {
    const existing = byLabel.get(t.label);
    const sameSection =
      existing !== undefined &&
      sectionName(current.sections, existing.section_key) !== null &&
      sectionName(current.sections, existing.section_key) === sectionName(draft.sections, t.section_key);
    const match = existing && (t.max_seats >= existing.max_seats || sameSection) ? existing : undefined;
    return {
      ...t,
      uid: match?.uid ?? `${uidPrefix}${i}`,
      id: match?.id ?? null,
      section_key: t.section_key === null ? null : (keyByDraftKey.get(t.section_key) ?? null),
    };
  });
  const uidByLabel = new Map(tables.map((t) => [t.label, t.uid]));

  const usedCombos = new Set<string>();
  const combinations: EditorCombination[] = draft.combinations.map((c, i) => {
    const match = current.combinations.find((x) => norm(x.name) === norm(c.name) && !usedCombos.has(x.key));
    if (match) usedCombos.add(match.key);
    return {
      key: match?.key ?? `${uidPrefix}c${i}`,
      id: match?.id ?? null,
      name: c.name,
      uids: c.table_labels.flatMap((l) => uidByLabel.get(l) ?? []),
      seats: null,
    };
  });

  return { sections, tables, combinations };
}

export interface DraftChanges {
  /** Current tables the draft updates in place. */
  kept: string[];
  /** Current tables whose label the draft reuses for a different table. */
  replaced: string[];
  /** Current tables the draft leaves out. */
  archived: string[];
}

/** What saving `next` (a floor with a draft applied) does to `current`'s tables. */
export function draftChanges(current: EditorFloor, next: EditorFloor): DraftChanges {
  const keptIds = new Set(next.tables.flatMap((t) => (t.id ? [t.id] : [])));
  const nextLabels = new Set(next.tables.map((t) => t.label));
  const saved = current.tables.filter((t) => t.id);
  return {
    kept: saved.filter((t) => keptIds.has(t.id!)).map((t) => t.label),
    replaced: saved.filter((t) => !keptIds.has(t.id!) && nextLabels.has(t.label)).map((t) => t.label),
    archived: saved.filter((t) => !keptIds.has(t.id!) && !nextLabels.has(t.label)).map((t) => t.label),
  };
}

/** What the modal does with the location's latest draft when it opens. */
export function resumeAction(draft: FloorDraftOut | null, applied: boolean): "poll" | "offer" | "none" {
  if (!draft) return "none";
  if (draft.status === "queued" || draft.status === "running") return "poll";
  if (draft.status === "done" && draft.result && !applied) return "offer";
  return "none";
}

// Per-tab memory of the draft in progress and of drafts already loaded: a
// convenience only (the server's latest draft is the source of truth), so
// every access survives storage that is missing or throws.
const draftKey = (locationId: string) => `floorDraft:${locationId}`;
const appliedKey = (draftId: string) => `floorDraftApplied:${draftId}`;

export function rememberDraft(locationId: string, draftId: string): void {
  try {
    sessionStorage.setItem(draftKey(locationId), draftId);
  } catch {
    /* storage unavailable */
  }
}

export function rememberedDraft(locationId: string): string | null {
  try {
    return sessionStorage.getItem(draftKey(locationId));
  } catch {
    return null;
  }
}

export function markDraftApplied(draftId: string): void {
  try {
    sessionStorage.setItem(appliedKey(draftId), "1");
  } catch {
    /* storage unavailable */
  }
}

export function draftApplied(draftId: string): boolean {
  try {
    return sessionStorage.getItem(appliedKey(draftId)) === "1";
  } catch {
    return false;
  }
}
