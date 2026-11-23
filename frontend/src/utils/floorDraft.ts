/** AI floor-plan drafts (decision 101): input checks and loading a draft into the editor. */
import type { FloorIn, SectionIO } from "../types/reservations";
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
 * combinations replace the current ones. A draft table whose label matches a
 * current table keeps that table's id (and uid), and likewise sections and
 * combinations by name, so saving updates those rows instead of archiving
 * them and creating new ones: their bookings and history stay attached.
 * Nothing is saved; the result is unsaved changes.
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

  const byLabel = new Map(current.tables.map((t) => [t.label, t]));
  const tables: EditorTable[] = draft.tables.map((t, i) => {
    const match = byLabel.get(t.label);
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
