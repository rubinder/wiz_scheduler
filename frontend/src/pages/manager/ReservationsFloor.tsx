import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import { getConfig, getFloor, saveFloor } from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { FloorOut } from "../../types/reservations";
import {
  CANVAS,
  clampTable,
  editorToFloorIn,
  errorKey,
  firstFreeSpot,
  floorToEditor,
  labelsForIds,
  moveTable,
  nextLabel,
  nextSectionKey,
} from "../../utils/reservations";
import type { EditorFloor, EditorTable } from "../../utils/reservations";
import { border, text } from "../../theme";

const ROTATIONS = [0, 90, 180, 270] as const;

interface Drag {
  uid: string;
  pointerId: number;
  /** Pointer position in floor units when the drag started. */
  start: { x: number; y: number };
  /** Table position when the drag started. */
  origin: { x: number; y: number };
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
    </label>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="glass-card p-4 space-y-3">
      <h2 className={`text-lg font-semibold ${text.heading}`}>{title}</h2>
      {children}
    </section>
  );
}

/** A number input that lets the field be blank while typing; blank or invalid is not sent upward. */
function NumberInput({
  value,
  onChange,
  className,
  min,
  step,
}: {
  value: number;
  onChange: (n: number) => void;
  className: string;
  min?: number;
  step?: number;
}) {
  const [raw, setRaw] = useState(String(value));
  useEffect(() => {
    setRaw((r) => (Number(r) === value && r.trim() !== "" ? r : String(value)));
  }, [value]);
  return (
    <input
      type="number"
      min={min}
      step={step}
      className={className}
      value={raw}
      onChange={(e) => {
        setRaw(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isFinite(n)) onChange(n);
      }}
      onBlur={() => setRaw(String(value))}
    />
  );
}

export default function ReservationsFloor() {
  const { t } = useLanguage();
  const r = t.reservations;
  const f = r.floor;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationsFailed, setLocationsFailed] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [loaded, setLoaded] = useState<FloorOut | null>(null);
  const [floor, setFloor] = useState<EditorFloor | null>(null);
  const [baseline, setBaseline] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<{ text: string; detail: string } | null>(null);
  const currentLocation = useRef("");
  const newUid = useRef(0);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<Drag | null>(null);

  const errorText = (detail: unknown): string => {
    const key = errorKey(detail).split(".")[2];
    return (r.errors as Record<string, string>)[key] ?? r.errors.generic;
  };

  const dirty = useMemo(
    () => floor !== null && JSON.stringify(editorToFloorIn(floor)) !== baseline,
    [floor, baseline],
  );

  const applyLoaded = (out: FloorOut, keepSelectedLabel?: string) => {
    const ed = floorToEditor(out);
    setLoaded(out);
    setFloor(ed);
    setBaseline(JSON.stringify(editorToFloorIn(ed)));
    setSelected(
      keepSelectedLabel === undefined ? null : (ed.tables.find((x) => x.label === keepSelectedLabel)?.uid ?? null),
    );
  };

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setEnabled(cfg.enabled);
        if (!cfg.enabled) return;
        const locs = await listLocations();
        setLocations(locs);
        if (locs.length > 0) setLocationId(locs[0].id);
      } catch (e) {
        setEnabled(true);
        setLocationsFailed(true);
        setLoadError(errorText(e instanceof ApiError ? e.data : null));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    currentLocation.current = locationId;
    drag.current = null;
    if (!locationId) return;
    let cancelled = false;
    setLoaded(null);
    setFloor(null);
    setBaseline("");
    setSelected(null);
    setSaved(false);
    setSaveError(null);
    setLoadError("");
    getFloor(locationId)
      .then((out) => {
        if (!cancelled) applyLoaded(out);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(errorText(e instanceof ApiError ? e.data : null));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId]);

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const edit = (fn: (fl: EditorFloor) => EditorFloor) => {
    setFloor((fl) => (fl ? fn(fl) : fl));
    setSaved(false);
  };
  const patchTable = (uid: string, p: Partial<EditorTable>) =>
    edit((fl) => ({
      ...fl,
      tables: fl.tables.map((x) => {
        if (x.uid !== uid) return x;
        const next = { ...x, ...p };
        // A resize or turn may push the table off the canvas.
        return { ...next, ...clampTable(next) };
      }),
      // A table that can no longer be combined leaves every combination.
      combinations:
        p.combinable === false
          ? fl.combinations.map((c) =>
              c.uids.includes(uid) ? { ...c, uids: c.uids.filter((u) => u !== uid), seats: null } : c,
            )
          : fl.combinations,
    }));
  const removeTable = (uid: string) => {
    edit((fl) => ({
      ...fl,
      tables: fl.tables.filter((x) => x.uid !== uid),
      combinations: fl.combinations.map((c) =>
        c.uids.includes(uid) ? { ...c, uids: c.uids.filter((u) => u !== uid), seats: null } : c,
      ),
    }));
    setSelected((s) => (s === uid ? null : s));
  };
  const addTable = () => {
    if (!floor) return;
    newUid.current += 1;
    const uid = `new:${newUid.current}`;
    const spot = firstFreeSpot(floor.tables);
    const table: EditorTable = {
      uid,
      id: null,
      label: nextLabel(floor.tables.map((x) => x.label)),
      section_key: null,
      min_seats: 1,
      max_seats: 2,
      combinable: false,
      x: spot.x,
      y: spot.y,
      w: 2,
      h: 2,
      rotation: 0,
    };
    edit((fl) => ({ ...fl, tables: [...fl.tables, table] }));
    setSelected(uid);
  };

  /** Pointer position in floor units, through the SVG's screen CTM. */
  const toFloor = (clientX: number, clientY: number): { x: number; y: number } | null => {
    const ctm = svgRef.current?.getScreenCTM();
    if (!ctm) return null;
    const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  };
  const onPointerDown = (e: PointerEvent<SVGGElement>, tbl: EditorTable) => {
    if (e.button !== 0) return;
    setSelected(tbl.uid);
    const start = toFloor(e.clientX, e.clientY);
    if (!start) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { uid: tbl.uid, pointerId: e.pointerId, start, origin: { x: tbl.x, y: tbl.y } };
  };
  const onPointerMove = (e: PointerEvent<SVGGElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const p = toFloor(e.clientX, e.clientY);
    if (!p) return;
    const tbl = floor?.tables.find((x) => x.uid === d.uid);
    if (!tbl) return;
    const pos = moveTable({ ...tbl, ...d.origin }, p.x - d.start.x, p.y - d.start.y);
    if (pos.x !== tbl.x || pos.y !== tbl.y) patchTable(d.uid, pos);
  };
  const endDrag = (e: PointerEvent<SVGGElement>) => {
    if (drag.current?.pointerId !== e.pointerId) return;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };
  const onTableKey = (e: KeyboardEvent<SVGGElement>, tbl: EditorTable) => {
    const step = e.shiftKey ? 2 : 0.5;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    if (e.key in moves) {
      e.preventDefault();
      setSelected(tbl.uid);
      const [dx, dy] = moves[e.key];
      patchTable(tbl.uid, moveTable(tbl, dx, dy));
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeTable(tbl.uid);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setSelected(tbl.uid);
    }
  };

  const onLocationChange = (id: string) => {
    if (dirty && !window.confirm(f.discardConfirm)) return;
    setLocationId(id);
  };

  const onSave = async () => {
    if (!floor) return;
    setSaving(true);
    setSaved(false);
    setSaveError(null);
    const forLocation = locationId;
    const selectedLabel = floor.tables.find((x) => x.uid === selected)?.label;
    try {
      const out = await saveFloor(forLocation, editorToFloorIn(floor));
      if (currentLocation.current !== forLocation) return;
      applyLoaded(out, selectedLabel);
      setSaved(true);
    } catch (e) {
      if (currentLocation.current !== forLocation) return;
      // The edits stay in `floor`; only the error is shown.
      const data = e instanceof ApiError ? e.data : null;
      const d = (data && typeof data === "object" ? data : {}) as {
        code?: string;
        message?: string;
        table_ids?: string[];
      };
      let detail = typeof d.message === "string" ? d.message : "";
      if (d.code === "table_has_bookings" && Array.isArray(d.table_ids)) {
        detail = f.bookedTables.replace("{labels}", labelsForIds(d.table_ids, loaded?.tables ?? []).join(", "));
      } else if (Array.isArray(data)) {
        detail = (data as { loc?: unknown[]; msg?: string }[])
          .map((v) => `${(v.loc ?? []).filter((p) => p !== "body").join(".")}: ${v.msg ?? ""}`)
          .join("; ");
      }
      setSaveError({ text: Array.isArray(data) ? f.saveFailed : `${errorText(data)} ${f.saveFailed}`, detail });
    } finally {
      setSaving(false);
    }
  };

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const sel = floor?.tables.find((x) => x.uid === selected) ?? null;
  const combinable = floor?.tables.filter((x) => x.combinable) ?? [];
  const labelOf = (uid: string) => floor?.tables.find((x) => x.uid === uid)?.label ?? "";

  return (
    <div className="space-y-4">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{f.title}</h1>
      <p className={`text-sm ${text.muted}`}>{f.description}</p>

      {locations.length === 0 && locationsFailed ? null : locations.length === 0 ? (
        <div className="glass-card p-6 text-sm">{f.noLocations}</div>
      ) : (
        <Field label={f.location}>
          <select
            className={`${input} block`}
            disabled={saving}
            value={locationId}
            onChange={(e) => onLocationChange(e.target.value)}
          >
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {loadError && <div className="glass-alert-error">{loadError}</div>}
      {locationId && !floor && !loadError && <p className={text.muted}>{r.common.loading}</p>}

      {floor && (
        <>
          {dirty && (
            <div className="glass-alert-warning" role="status">
              {f.unsaved}
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="glass-card space-y-2 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" className="glass-btn-secondary text-sm" onClick={addTable}>
                  + {f.addTable}
                </button>
                <span className={`text-xs ${text.muted}`}>{f.keyboardHint}</span>
              </div>
              <svg
                ref={svgRef}
                viewBox={`0 0 ${CANVAS.width} ${CANVAS.height}`}
                role="group"
                aria-label={f.canvasLabel}
                className="block w-full touch-none select-none rounded border bg-white/60"
                onPointerDown={(e) => {
                  if (e.target === e.currentTarget) setSelected(null);
                }}
              >
                <defs>
                  <pattern id="floor-grid" width="1" height="1" patternUnits="userSpaceOnUse">
                    <path d="M 1 0 L 0 0 0 1" fill="none" stroke="currentColor" strokeWidth="0.02" />
                  </pattern>
                </defs>
                <rect
                  width={CANVAS.width}
                  height={CANVAS.height}
                  fill="url(#floor-grid)"
                  className="text-sage pointer-events-none"
                />
                {floor.tables.map((tbl) => {
                  const cx = tbl.x + tbl.w / 2;
                  const cy = tbl.y + tbl.h / 2;
                  const isSel = tbl.uid === selected;
                  return (
                    <g
                      key={tbl.uid}
                      role="button"
                      tabIndex={0}
                      aria-pressed={isSel}
                      aria-label={f.tableAria
                        .replace("{label}", tbl.label)
                        .replace("{min}", String(tbl.min_seats))
                        .replace("{max}", String(tbl.max_seats))}
                      className="cursor-move outline-none"
                      onPointerDown={(e) => onPointerDown(e, tbl)}
                      onPointerMove={onPointerMove}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                      onKeyDown={(e) => onTableKey(e, tbl)}
                      onFocus={() => setSelected(tbl.uid)}
                    >
                      <rect
                        x={tbl.x}
                        y={tbl.y}
                        width={tbl.w}
                        height={tbl.h}
                        rx={0.3}
                        transform={`rotate(${tbl.rotation} ${cx} ${cy})`}
                        className={isSel ? "fill-accent-200 stroke-accent-dark" : "fill-white stroke-sage-dark"}
                        strokeWidth={isSel ? 0.12 : 0.06}
                      />
                      <text
                        x={cx}
                        y={cy - 0.1}
                        textAnchor="middle"
                        fontSize={0.55}
                        className="pointer-events-none fill-gray-800 font-semibold"
                      >
                        {tbl.label}
                      </text>
                      <text
                        x={cx}
                        y={cy + 0.55}
                        textAnchor="middle"
                        fontSize={0.42}
                        className="pointer-events-none fill-gray-500"
                      >
                        {tbl.min_seats}–{tbl.max_seats}
                      </text>
                    </g>
                  );
                })}
              </svg>
              {floor.tables.length === 0 && <p className={`text-sm ${text.muted}`}>{f.empty}</p>}
            </div>

            <Panel title={f.tableHeading}>
              {!sel ? (
                <p className={`text-sm ${text.muted}`}>{f.noSelection}</p>
              ) : (
                <div className="space-y-3">
                  <Field label={f.label}>
                    <input
                      className={`${input} w-full`}
                      value={sel.label}
                      onChange={(e) => patchTable(sel.uid, { label: e.target.value })}
                    />
                  </Field>
                  <Field label={f.section}>
                    <select
                      className={`${input} w-full`}
                      value={sel.section_key ?? ""}
                      onChange={(e) => patchTable(sel.uid, { section_key: e.target.value || null })}
                    >
                      <option value="">{f.noSection}</option>
                      {floor.sections.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.name || "—"}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label={f.minSeats}>
                      <NumberInput
                        min={1}
                        step={1}
                        className={`${input} w-full`}
                        value={sel.min_seats}
                        onChange={(n) => patchTable(sel.uid, { min_seats: n })}
                      />
                    </Field>
                    <Field label={f.maxSeats}>
                      <NumberInput
                        min={1}
                        step={1}
                        className={`${input} w-full`}
                        value={sel.max_seats}
                        onChange={(n) => patchTable(sel.uid, { max_seats: n })}
                      />
                    </Field>
                    <Field label={f.width}>
                      <NumberInput
                        min={0.5}
                        step={0.5}
                        className={`${input} w-full`}
                        value={sel.w}
                        onChange={(n) => n > 0 && patchTable(sel.uid, { w: n })}
                      />
                    </Field>
                    <Field label={f.height}>
                      <NumberInput
                        min={0.5}
                        step={0.5}
                        className={`${input} w-full`}
                        value={sel.h}
                        onChange={(n) => n > 0 && patchTable(sel.uid, { h: n })}
                      />
                    </Field>
                  </div>
                  <Field label={f.rotation}>
                    <select
                      className={`${input} w-full`}
                      value={ROTATIONS.includes(sel.rotation as (typeof ROTATIONS)[number]) ? sel.rotation : 0}
                      onChange={(e) => patchTable(sel.uid, { rotation: Number(e.target.value) })}
                    >
                      {ROTATIONS.map((deg) => (
                        <option key={deg} value={deg}>
                          {deg}°
                        </option>
                      ))}
                    </select>
                  </Field>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={sel.combinable}
                      onChange={(e) => patchTable(sel.uid, { combinable: e.target.checked })}
                    />
                    {f.combinable}
                  </label>
                  <div className="space-y-1">
                    <button type="button" className="glass-btn-secondary text-sm" onClick={() => removeTable(sel.uid)}>
                      {f.deleteTable}
                    </button>
                    <p className={`text-xs ${text.muted}`}>{f.deleteHint}</p>
                  </div>
                </div>
              )}
            </Panel>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title={f.sections}>
              {floor.sections.map((s, i) => (
                <div key={s.key} className="flex items-center gap-2">
                  <input
                    className={`${input} flex-1`}
                    aria-label={`${f.sectionName} ${i + 1}`}
                    value={s.name}
                    onChange={(e) =>
                      edit((fl) => ({
                        ...fl,
                        sections: fl.sections.map((x) => (x.key === s.key ? { ...x, name: e.target.value } : x)),
                      }))
                    }
                  />
                  <button
                    type="button"
                    className={`text-sm ${text.muted}`}
                    aria-label={`${f.removeSection} ${s.name || i + 1}`}
                    onClick={() =>
                      edit((fl) => ({
                        ...fl,
                        sections: fl.sections.filter((x) => x.key !== s.key),
                        tables: fl.tables.map((x) => (x.section_key === s.key ? { ...x, section_key: null } : x)),
                      }))
                    }
                  >
                    {r.common.remove}
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="glass-btn-secondary text-sm"
                onClick={() =>
                  edit((fl) => ({
                    ...fl,
                    sections: [
                      ...fl.sections,
                      {
                        id: null,
                        key: nextSectionKey(fl.sections.map((s) => s.key)),
                        name: "",
                        sort_order: fl.sections.length,
                      },
                    ],
                  }))
                }
              >
                + {f.addSection}
              </button>
            </Panel>

            <Panel title={f.combinations}>
              {combinable.length === 0 && <p className={`text-sm ${text.muted}`}>{f.noCombinable}</p>}
              {floor.combinations.map((c, i) => (
                <div key={c.key} className="space-y-2 rounded border p-3">
                  <div className="flex items-center gap-2">
                    <input
                      className={`${input} flex-1`}
                      aria-label={`${f.combinationName} ${i + 1}`}
                      value={c.name}
                      onChange={(e) =>
                        edit((fl) => ({
                          ...fl,
                          combinations: fl.combinations.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)),
                        }))
                      }
                    />
                    <button
                      type="button"
                      className={`text-sm ${text.muted}`}
                      aria-label={`${f.removeCombination} ${c.name || i + 1}`}
                      onClick={() =>
                        edit((fl) => ({ ...fl, combinations: fl.combinations.filter((_, j) => j !== i) }))
                      }
                    >
                      {r.common.remove}
                    </button>
                  </div>
                  <fieldset className="flex flex-wrap gap-3">
                    <legend className="sr-only">
                      {f.combinationTables} {c.name || i + 1}
                    </legend>
                    {combinable.map((tbl) => (
                      <label key={tbl.uid} className="flex items-center gap-1 text-sm">
                        <input
                          type="checkbox"
                          checked={c.uids.includes(tbl.uid)}
                          onChange={(e) =>
                            edit((fl) => ({
                              ...fl,
                              combinations: fl.combinations.map((x, j) =>
                                j !== i
                                  ? x
                                  : {
                                      ...x,
                                      seats: null,
                                      uids: e.target.checked
                                        ? [...x.uids, tbl.uid]
                                        : x.uids.filter((u) => u !== tbl.uid),
                                    },
                              ),
                            }))
                          }
                        />
                        {tbl.label}
                      </label>
                    ))}
                  </fieldset>
                  <p className={`text-xs ${text.muted}`}>
                    {c.uids.map(labelOf).join(" + ")}
                    {c.uids.length > 0 && " · "}
                    {c.seats
                      ? f.seatsRange.replace("{min}", String(c.seats.min)).replace("{max}", String(c.seats.max))
                      : f.seatsPending}
                  </p>
                </div>
              ))}
              <button
                type="button"
                className="glass-btn-secondary text-sm"
                onClick={() => {
                  newUid.current += 1;
                  const key = `new-c:${newUid.current}`;
                  edit((fl) => ({
                    ...fl,
                    combinations: [...fl.combinations, { key, id: null, name: "", uids: [], seats: null }],
                  }));
                }}
              >
                + {f.addCombination}
              </button>
            </Panel>
          </div>

          {saveError && (
            <div className="glass-alert-error" role="alert">
              <p>{saveError.text}</p>
              {saveError.detail && <p className="text-sm">{saveError.detail}</p>}
            </div>
          )}
          {saved && !dirty && <div className="glass-alert-success">{r.common.saved}</div>}
          <button type="button" className="glass-btn-primary" disabled={saving} onClick={onSave}>
            {saving ? r.common.saving : r.common.save}
          </button>
        </>
      )}
    </div>
  );
}
