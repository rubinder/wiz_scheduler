import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { createFloorDraft, getFloorDraft } from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { FloorDraftOut } from "../../types/reservations";
import {
  DRAFT_ACCEPT,
  DRAFT_LIMITS,
  downscaleImage,
  draftErrorKey,
  validateDraftInput,
} from "../../utils/floorDraft";
import type { DraftErrorKey } from "../../utils/floorDraft";
import { border, text } from "../../theme";

const POLL_MS = 2000;

interface Item {
  key: number;
  file: File;
  caption: string;
}

/**
 * "Draft from photos (AI)" (decision 101): pick photos or PDF blueprints,
 * caption them, add notes, submit, then poll until the draft is done. A done
 * draft goes to `onDraft`; the page loads it into the editor unsaved.
 */
export default function FloorDraftModal({
  locationId,
  isAdmin,
  onDraft,
  onClose,
}: {
  locationId: string;
  isAdmin: boolean;
  onDraft: (draft: FloorDraftOut) => void;
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const a = t.reservations.floor.ai;
  const [items, setItems] = useState<Item[]>([]);
  const [notes, setNotes] = useState("");
  const [phase, setPhase] = useState<"edit" | "preparing" | "working">("edit");
  const [error, setError] = useState<DraftErrorKey | null>(null);
  const nextKey = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const busy = phase !== "edit";

  const addFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return;
    setError(null);
    setPhase("preparing");
    // Shrink large photos now, so the limits below apply to what is sent.
    const files = await Promise.all(Array.from(list).map(downscaleImage));
    if (!alive.current) return;
    setItems((prev) => [
      ...prev,
      ...files.map((file) => {
        nextKey.current += 1;
        return { key: nextKey.current, file, caption: "" };
      }),
    ]);
    setPhase("edit");
  };

  const poll = (draftId: string) => {
    timer.current = setInterval(async () => {
      try {
        const d = await getFloorDraft(locationId, draftId);
        if (!alive.current || d.status === "queued" || d.status === "running") return;
        if (timer.current) clearInterval(timer.current);
        if (d.status === "done" && d.result) {
          onDraft(d);
        } else {
          setError(draftErrorKey(d.error_code));
          setPhase("edit");
        }
      } catch {
        // A blip while polling: keep trying until the draft times out.
      }
    }, POLL_MS);
  };

  const submit = async () => {
    const problem = validateDraftInput(
      items.map((i) => i.file),
      items.map((i) => i.caption),
      notes,
    );
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setPhase("working");
    try {
      const d = await createFloorDraft(
        locationId,
        items.map((i) => ({ file: i.file, caption: i.caption })),
        notes,
      );
      if (alive.current) poll(d.id);
    } catch (e) {
      if (!alive.current) return;
      const data = e instanceof ApiError ? e.data : null;
      const code = data && typeof data === "object" ? (data as { code?: string }).code : null;
      setError(draftErrorKey(code));
      setPhase("edit");
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="floor-draft-title"
        className="glass-card max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto p-6"
      >
        <h2 id="floor-draft-title" className={`text-lg font-semibold ${text.heading}`}>
          {a.title}
        </h2>
        <p className={`text-sm ${text.muted}`}>{a.intro}</p>

        <div className="space-y-2">
          <label className="block">
            <span className={`glass-label-sm ${text.muted}`}>{a.files}</span>
            <input
              type="file"
              multiple
              accept={DRAFT_ACCEPT}
              disabled={busy || items.length >= DRAFT_LIMITS.files}
              className="block w-full text-sm"
              onChange={(e) => {
                void addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
          <p className={`text-xs ${text.muted}`}>{a.filesHint}</p>
          {items.map((it) => (
            <div key={it.key} className="space-y-1 rounded border p-2">
              <div className="flex items-center gap-2">
                <span className="flex-1 truncate text-sm">{it.file.name}</span>
                <button
                  type="button"
                  className={`text-sm ${text.muted}`}
                  disabled={busy}
                  aria-label={`${a.removeFile} ${it.file.name}`}
                  onClick={() => setItems((prev) => prev.filter((x) => x.key !== it.key))}
                >
                  ✕
                </button>
              </div>
              <input
                className={`${input} w-full`}
                aria-label={`${a.caption}: ${it.file.name}`}
                placeholder={a.captionPlaceholder}
                maxLength={DRAFT_LIMITS.captionChars}
                disabled={busy}
                value={it.caption}
                onChange={(e) =>
                  setItems((prev) => prev.map((x) => (x.key === it.key ? { ...x, caption: e.target.value } : x)))
                }
              />
            </div>
          ))}
        </div>

        <label className="block">
          <span className={`glass-label-sm ${text.muted}`}>{a.notes}</span>
          <textarea
            className={`${input} block w-full`}
            rows={3}
            maxLength={DRAFT_LIMITS.notesChars}
            placeholder={a.notesPlaceholder}
            disabled={busy}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </label>

        <p className={`text-xs ${text.muted}`}>{a.cost}</p>

        {phase === "preparing" && <p className={`text-sm ${text.muted}`}>{a.preparing}</p>}
        {phase === "working" && (
          <p className={`text-sm ${text.muted}`} role="status">
            {a.working}
          </p>
        )}
        {error && (
          <div className="glass-alert-error space-y-1" role="alert">
            <p>{a.errors[error]}</p>
            {error === "ai_credits_required" &&
              (isAdmin ? (
                <Link to="/manager/schedule" className="text-sm underline">
                  {a.buyCredits}
                </Link>
              ) : (
                <p className="text-sm">{t.access.billingAdminOnly}</p>
              ))}
          </div>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="glass-btn-secondary text-sm" onClick={onClose}>
            {a.cancel}
          </button>
          <button type="button" className="glass-btn-primary text-sm" disabled={busy} onClick={submit}>
            {a.submit}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
