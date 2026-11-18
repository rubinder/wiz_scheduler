import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import {
  approveReservation,
  createReservation,
  declineReservation,
  getConfig,
  getFloor,
  getSlots,
  listApprovals,
  listReservations,
  seatWalkIn,
  setReservationStatus,
} from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { FloorOut, ReservationOut, SettableStatus, SlotOut } from "../../types/reservations";
import {
  CANVAS,
  errorCode,
  formatLocalDateTime,
  formatLocalTime,
  groupBySeating,
  localDateString,
  tableStates,
  validParty,
} from "../../utils/reservations";
import type { TableState } from "../../utils/reservations";
import { border, text } from "../../theme";

const REFRESH_MS = 60_000;

const STATE_CLASS: Record<TableState, string> = {
  free: "fill-white stroke-sage-dark",
  seated: "fill-red-200 stroke-red-700",
  upcoming: "fill-amber-100 stroke-amber-600",
};

const LEGEND_CLASS: Record<TableState, string> = {
  free: "bg-white border-sage-dark",
  seated: "bg-red-200 border-red-700",
  upcoming: "bg-amber-100 border-amber-600",
};

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

const blankToNull = (s: string): string | null => (s.trim() === "" ? null : s.trim());

/** Server detail -> { code, message }; anything else -> nulls. */
function detailOf(e: unknown): { data: unknown; code: string | null; message: string } {
  const data = e instanceof ApiError ? e.data : null;
  const d = (data && typeof data === "object" && !Array.isArray(data) ? data : {}) as {
    code?: unknown;
    message?: unknown;
  };
  return {
    data,
    code: typeof d.code === "string" ? d.code : null,
    message: typeof d.message === "string" ? d.message : "",
  };
}

export default function ReservationsHost() {
  const { t, lang } = useLanguage();
  const r = t.reservations;
  const h = r.host;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  /** The initial config/locations load failed; shown with a Retry button. */
  const [initError, setInitError] = useState("");
  const [locationId, setLocationId] = useState("");
  const [date, setDate] = useState("");
  /** The location `date` was defaulted for; the list waits until it matches, so a switch fetches once. */
  const [dateFor, setDateFor] = useState("");
  const [floor, setFloor] = useState<FloorOut | null>(null);
  const [floorError, setFloorError] = useState("");
  const [list, setList] = useState<ReservationOut[] | null>(null);
  const [listError, setListError] = useState("");
  /** Live approval requests for the location on every day; independent of `date`. */
  const [approvals, setApprovals] = useState<ReservationOut[] | null>(null);
  const [approvalsError, setApprovalsError] = useState("");
  const [now, setNow] = useState(() => new Date());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  // New booking drawer
  const [drawer, setDrawer] = useState<"none" | "booking" | "walkIn">("none");
  const [bPartySize, setBPartySize] = useState("2");
  const [bDate, setBDate] = useState("");
  const [slots, setSlots] = useState<SlotOut[] | null>(null);
  const [slot, setSlot] = useState<SlotOut | null>(null);
  const [bName, setBName] = useState("");
  const [bPhone, setBPhone] = useState("");
  const [bEmail, setBEmail] = useState("");
  const [bNotes, setBNotes] = useState("");
  const [bBusy, setBBusy] = useState(false);
  const [bError, setBError] = useState("");
  const [bCode, setBCode] = useState<string | null>(null);

  // Walk-in
  const [wPartySize, setWPartySize] = useState("2");
  const [wName, setWName] = useState("");
  const [wPhone, setWPhone] = useState("");
  const [wNotes, setWNotes] = useState("");
  const [wBusy, setWBusy] = useState(false);
  const [wError, setWError] = useState("");
  const [wSeated, setWSeated] = useState<string[] | null>(null);

  /** "location|date" the page is showing; a response for any other view is dropped. */
  const currentView = useRef("");
  const listSeq = useRef(0);
  /** Location the approvals panel is showing; a response for another is dropped. */
  const currentLocation = useRef("");
  const approvalsSeq = useRef(0);
  const slotSeq = useRef(0);

  const location = locations.find((l) => l.id === locationId) ?? null;
  const tz = location?.timezone ?? "UTC";

  const errorText = (detail: unknown): string => {
    const code = errorCode(detail);
    return (code && (r.errors as Record<string, string>)[code]) || r.errors.generic;
  };
  /** Translated message, plus the server's own message when it adds something. */
  const fullError = (e: unknown): string => {
    const { data, message } = detailOf(e);
    const base = errorText(data);
    return message && message !== base ? `${base} (${message})` : base;
  };

  const loadInitial = async () => {
    setInitError("");
    try {
      const cfg = await getConfig();
      setEnabled(cfg.enabled);
      if (!cfg.enabled) return;
      const locs = await listLocations();
      setLocations(locs);
      if (locs.length > 0) setLocationId(locs[0].id);
    } catch (e) {
      setEnabled(true);
      setInitError(errorText(e instanceof ApiError ? e.data : null));
    }
  };

  useEffect(() => {
    void loadInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new location starts on that location's today, with a fresh floor and closed drawer.
  useEffect(() => {
    slotSeq.current += 1;
    setDrawer("none");
    setSlots(null);
    setSlot(null);
    setBCode(null);
    setWSeated(null);
    setActionError("");
    if (!location) return;
    setDate(localDateString(new Date(), location.timezone));
    setDateFor(location.id);
    let cancelled = false;
    setFloor(null);
    setFloorError("");
    getFloor(location.id)
      .then((out) => {
        if (!cancelled) setFloor(out);
      })
      .catch((e) => {
        if (!cancelled) setFloorError(errorText(e instanceof ApiError ? e.data : null));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location]);

  /** Reload the day's list; a response for an older request or another view is ignored. */
  const loadList = async () => {
    const view = `${locationId}|${date}`;
    if (!locationId || !date) return;
    const seq = ++listSeq.current;
    const latest = () => seq === listSeq.current && currentView.current === view;
    try {
      const out = await listReservations(locationId, date);
      if (!latest()) return;
      setList(out);
      setListError("");
      setNow(new Date());
    } catch (e) {
      if (latest()) setListError(errorText(e instanceof ApiError ? e.data : null));
    }
  };

  /** Reload the approval queue; a response for an older request or another location is ignored. */
  const loadApprovals = async () => {
    const forLocation = locationId;
    if (!forLocation) return;
    const seq = ++approvalsSeq.current;
    const latest = () => seq === approvalsSeq.current && currentLocation.current === forLocation;
    try {
      const out = await listApprovals(forLocation);
      if (!latest()) return;
      setApprovals(out);
      setApprovalsError("");
    } catch (e) {
      if (latest()) setApprovalsError(errorText(e instanceof ApiError ? e.data : null));
    }
  };

  useEffect(() => {
    currentLocation.current = locationId;
    approvalsSeq.current += 1;
    setApprovals(null);
    setApprovalsError("");
    if (!locationId) return;
    void loadApprovals();
    const timer = window.setInterval(() => void loadApprovals(), REFRESH_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId]);

  useEffect(() => {
    currentView.current = `${locationId}|${date}`;
    listSeq.current += 1;
    setList(null);
    setListError("");
    if (!locationId || !date || dateFor !== locationId) return;
    void loadList();
    const timer = window.setInterval(() => void loadList(), REFRESH_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId, date, dateFor]);

  const groups = useMemo(() => groupBySeating(list ?? [], tz), [list, tz]);
  // Live state is only meaningful for today in the restaurant's zone.
  const isToday = location !== null && date === localDateString(now, location.timezone);
  // Display only: the server decides what is bookable.
  const states = useMemo(
    () => (isToday ? tableStates(floor?.tables ?? [], list ?? [], now) : {}),
    [isToday, floor, list, now],
  );

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const statusName = (s: string): string => (r.status as Record<string, string>)[s] ?? s;
  const time = (iso: string) => formatLocalTime(iso, tz, lang);

  const act = async (res: ReservationOut, run: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    const view = currentView.current;
    const forLocation = currentLocation.current;
    setBusyId(res.id);
    setActionError("");
    try {
      await run();
    } catch (e) {
      if (currentView.current === view) setActionError(fullError(e));
    } finally {
      setBusyId(null);
      if (currentView.current === view) void loadList();
      if (currentLocation.current === forLocation) void loadApprovals();
    }
  };
  const setStatus = (res: ReservationOut, s: SettableStatus, confirmText?: string) =>
    act(res, () => setReservationStatus(res.id, s), confirmText);

  const loadSlots = async (keepMessage = false) => {
    const forLocation = locationId;
    const seq = ++slotSeq.current;
    const latest = () => seq === slotSeq.current && currentView.current.startsWith(`${forLocation}|`);
    setSlots(null);
    setSlot(null);
    if (!keepMessage) {
      setBError("");
      setBCode(null);
    }
    try {
      // Staff view (no cutoff): the host can book inside the diner cutoff.
      const out = await getSlots(forLocation, Number(bPartySize), bDate, false);
      if (latest()) setSlots(out);
    } catch (e) {
      if (latest()) setBError(fullError(e));
    }
  };

  const onBook = async () => {
    if (!slot || bName.trim() === "") return;
    const forLocation = locationId;
    const stillHere = () => currentView.current.startsWith(`${forLocation}|`);
    setBBusy(true);
    setBError("");
    setBCode(null);
    try {
      const out = await createReservation(forLocation, {
        party_size: Number(bPartySize),
        // The slot's own ISO string, offset and all; never rebuilt here.
        start: slot.start,
        diner: { name: bName.trim(), phone: blankToNull(bPhone), email: blankToNull(bEmail) },
        notes: blankToNull(bNotes),
      });
      if (!stillHere()) return;
      setBCode(out.confirmation_code ?? "");
      setSlot(null);
      setSlots(null);
      setBName("");
      setBPhone("");
      setBEmail("");
      setBNotes("");
      void loadList();
    } catch (e) {
      if (!stillHere()) return;
      setBError(fullError(e));
      if (detailOf(e).code === "slot_unavailable") void loadSlots(true);
    } finally {
      setBBusy(false);
    }
  };

  const onWalkIn = async () => {
    const forLocation = locationId;
    const stillHere = () => currentView.current.startsWith(`${forLocation}|`);
    setWBusy(true);
    setWError("");
    setWSeated(null);
    try {
      const out = await seatWalkIn(forLocation, {
        party_size: Number(wPartySize),
        name: blankToNull(wName),
        phone: blankToNull(wPhone),
        notes: blankToNull(wNotes),
      });
      if (!stillHere()) return;
      setWSeated(out.tables);
      setWName("");
      setWPhone("");
      setWNotes("");
      void loadList();
    } catch (e) {
      if (!stillHere()) return;
      setWError(detailOf(e).code === "slot_unavailable" ? h.noTableFree : fullError(e));
    } finally {
      setWBusy(false);
    }
  };

  const openDrawer = (which: "booking" | "walkIn") => {
    setDrawer((d) => (d === which ? "none" : which));
    if (which === "booking") {
      setBDate(date);
      setSlots(null);
      setSlot(null);
      setBError("");
      setBCode(null);
    } else {
      setWError("");
      setWSeated(null);
    }
  };

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;

  const actionButton = (label: string, res: ReservationOut, onClick: () => void, primary = false) => (
    <button
      type="button"
      className={`${primary ? "glass-btn-primary" : "glass-btn-secondary"} text-xs`}
      disabled={busyId !== null}
      aria-label={`${label}: ${res.diner_name ?? ""} ${time(res.start)}`.trim()}
      onClick={onClick}
    >
      {label}
    </button>
  );

  const actionsFor = (res: ReservationOut): ReactNode => {
    switch (res.status) {
      case "booked":
        return (
          <>
            {actionButton(h.seat, res, () => void setStatus(res, "seated"), true)}
            {actionButton(h.noShow, res, () => void setStatus(res, "no_show", h.noShowConfirm))}
            {actionButton(h.cancel, res, () => void setStatus(res, "cancelled", h.cancelConfirm))}
          </>
        );
      case "seated":
        return actionButton(h.complete, res, () => void setStatus(res, "completed"), true);
      case "pending_approval":
        return (
          <>
            {actionButton(h.approve, res, () => void act(res, () => approveReservation(res.id)), true)}
            {actionButton(h.decline, res, () => void act(res, () => declineReservation(res.id), h.declineConfirm))}
          </>
        );
      default:
        return null;
    }
  };

  /** `withDate`: the approvals panel spans days, so its rows show date and time. */
  const row = (res: ReservationOut, withDate = false) => (
    <li key={res.id} className="rounded border p-3 space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className={`font-semibold ${text.primary}`}>
          {withDate && `${formatLocalDateTime(res.start, tz, lang)} · `}
          {res.diner_name || h.noName}
        </span>
        <span className={`text-xs ${text.muted}`}>{statusName(res.status)}</span>
      </div>
      <p className={`text-sm ${text.muted}`}>
        {h.party.replace("{n}", String(res.party_size))}
        {" · "}
        {res.tables.length > 0 ? h.tables.replace("{labels}", res.tables.join(", ")) : h.noTable}
        {res.diner_phone && ` · ${res.diner_phone}`}
      </p>
      {res.notes && <p className="text-sm">{res.notes}</p>}
      {busyId === res.id ? (
        <p className={`text-xs ${text.muted}`}>{r.common.loading}</p>
      ) : (
        <div className="flex flex-wrap gap-2">{actionsFor(res)}</div>
      )}
    </li>
  );

  return (
    <div className="space-y-4">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{h.title}</h1>
      <p className={`text-sm ${text.muted}`}>{h.description}</p>

      {initError && (
        <div className="glass-alert-error flex flex-wrap items-center gap-3" role="alert">
          <span>{initError}</span>
          <button type="button" className="glass-btn-secondary text-sm" onClick={() => void loadInitial()}>
            {h.retry}
          </button>
        </div>
      )}

      {locations.length === 0 && initError ? null : locations.length === 0 ? (
        <div className="glass-card p-6 text-sm">{h.noLocations}</div>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <Field label={h.location}>
            <select className={`${input} block`} value={locationId} onChange={(e) => setLocationId(e.target.value)}>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={h.date}>
            <input
              type="date"
              className={`${input} block`}
              value={date}
              onChange={(e) => e.target.value && setDate(e.target.value)}
            />
          </Field>
          <button type="button" className="glass-btn-primary text-sm" onClick={() => openDrawer("booking")}>
            + {h.newBooking}
          </button>
          <button
            type="button"
            className="glass-btn-secondary text-sm"
            disabled={!isToday}
            aria-describedby={isToday ? undefined : "walk-in-today-only"}
            onClick={() => openDrawer("walkIn")}
          >
            {h.walkIn}
          </button>
          {!isToday && (
            <span id="walk-in-today-only" className={`text-xs ${text.muted}`}>
              {h.walkInTodayOnly}
            </span>
          )}
        </div>
      )}

      {drawer === "booking" && location && (
        <Panel title={h.newBooking}>
          <div className="flex flex-wrap items-end gap-3">
            <Field label={h.partySize}>
              <input
                type="number"
                min={1}
                step={1}
                className={`${input} block w-24`}
                value={bPartySize}
                onChange={(e) => {
                  setBPartySize(e.target.value);
                  setSlots(null);
                  setSlot(null);
                }}
              />
            </Field>
            <Field label={h.date}>
              <input
                type="date"
                className={`${input} block`}
                value={bDate}
                onChange={(e) => {
                  setBDate(e.target.value);
                  setSlots(null);
                  setSlot(null);
                }}
              />
            </Field>
            <button
              type="button"
              className="glass-btn-secondary text-sm"
              disabled={!validParty(bPartySize) || !bDate || bBusy}
              onClick={() => void loadSlots()}
            >
              {h.showTimes}
            </button>
          </div>

          {slots && slots.length === 0 && <p className={`text-sm ${text.muted}`}>{h.noSlots}</p>}
          {slots && slots.length > 0 && (
            <fieldset className="space-y-2">
              <legend className={`glass-label-sm ${text.muted}`}>{h.chooseTime}</legend>
              <div className="flex flex-wrap gap-2">
                {slots.map((s) => (
                  <button
                    key={s.start}
                    type="button"
                    aria-pressed={slot?.start === s.start}
                    className={`${slot?.start === s.start ? "glass-btn-primary" : "glass-btn-secondary"} text-sm`}
                    onClick={() => setSlot(s)}
                  >
                    {time(s.start)}
                  </button>
                ))}
              </div>
            </fieldset>
          )}

          {slot && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={h.name}>
                <input className={`${input} w-full`} value={bName} onChange={(e) => setBName(e.target.value)} />
              </Field>
              <Field label={h.phone}>
                <input
                  type="tel"
                  className={`${input} w-full`}
                  placeholder="+12125550100"
                  value={bPhone}
                  onChange={(e) => setBPhone(e.target.value)}
                />
                <span className={`text-xs ${text.muted}`}>{h.phoneHint}</span>
              </Field>
              <Field label={h.email}>
                <input
                  type="email"
                  className={`${input} w-full`}
                  value={bEmail}
                  onChange={(e) => setBEmail(e.target.value)}
                />
              </Field>
              <Field label={h.notes}>
                <input className={`${input} w-full`} value={bNotes} onChange={(e) => setBNotes(e.target.value)} />
              </Field>
              <div className="sm:col-span-2">
                <button
                  type="button"
                  className="glass-btn-primary text-sm"
                  disabled={bBusy || bName.trim() === ""}
                  onClick={() => void onBook()}
                >
                  {bBusy ? h.booking : h.book.replace("{time}", time(slot.start))}
                </button>
              </div>
            </div>
          )}

          {bError && (
            <div className="glass-alert-error" role="alert">
              {bError}
            </div>
          )}
          {bCode !== null && (
            <div className="glass-alert-success" role="status">
              {bCode ? h.bookedCode.replace("{code}", bCode) : h.bookedNoCode}
            </div>
          )}
        </Panel>
      )}

      {drawer === "walkIn" && location && isToday && (
        <Panel title={h.walkIn}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={h.partySize}>
              <input
                type="number"
                min={1}
                step={1}
                className={`${input} w-full`}
                value={wPartySize}
                onChange={(e) => setWPartySize(e.target.value)}
              />
            </Field>
            <Field label={h.nameOptional}>
              <input className={`${input} w-full`} value={wName} onChange={(e) => setWName(e.target.value)} />
            </Field>
            <Field label={h.phone}>
              <input
                type="tel"
                className={`${input} w-full`}
                placeholder="+12125550100"
                value={wPhone}
                onChange={(e) => setWPhone(e.target.value)}
              />
              <span className={`text-xs ${text.muted}`}>{h.phoneHint}</span>
            </Field>
            <Field label={h.notes}>
              <input className={`${input} w-full`} value={wNotes} onChange={(e) => setWNotes(e.target.value)} />
            </Field>
          </div>
          <button
            type="button"
            className="glass-btn-primary text-sm"
            disabled={wBusy || !validParty(wPartySize)}
            onClick={() => void onWalkIn()}
          >
            {wBusy ? h.seating : h.seatNow}
          </button>
          {wError && (
            <div className="glass-alert-error" role="alert">
              {wError}
            </div>
          )}
          {wSeated && (
            <div className="glass-alert-success" role="status">
              {h.walkInSeated.replace("{labels}", wSeated.join(", "))}
            </div>
          )}
        </Panel>
      )}

      {actionError && (
        <div className="glass-alert-error" role="alert">
          {actionError}
        </div>
      )}

      {locationId && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <section className="glass-card space-y-2 p-3">
            <h2 className={`text-lg font-semibold ${text.heading}`}>{h.floorHeading}</h2>
            {floorError && (
              <div className="glass-alert-error" role="alert">
                {floorError}
              </div>
            )}
            {!isToday && <p className={`text-sm ${text.muted}`}>{h.todayOnly}</p>}
            {!floor && !floorError && <p className={text.muted}>{r.common.loading}</p>}
            {floor && (
              <>
                <svg
                  viewBox={`0 0 ${CANVAS.width} ${CANVAS.height}`}
                  role="group"
                  aria-label={h.canvasLabel}
                  className="block w-full select-none rounded border bg-white/60"
                >
                  {floor.tables.map((tbl) => {
                    const cx = tbl.x + tbl.w / 2;
                    const cy = tbl.y + tbl.h / 2;
                    const state = states[tbl.label] ?? "free";
                    // Status only means something today; other days just name the table.
                    const label = isToday
                      ? h.tableAria.replace("{label}", tbl.label).replace("{state}", h.state[state])
                      : r.floor.tableAria
                          .replace("{label}", tbl.label)
                          .replace("{min}", String(tbl.min_seats))
                          .replace("{max}", String(tbl.max_seats));
                    return (
                      <g key={tbl.id ?? tbl.label} role="img" aria-label={label}>
                        <title>{label}</title>
                        <rect
                          x={tbl.x}
                          y={tbl.y}
                          width={tbl.w}
                          height={tbl.h}
                          rx={0.3}
                          transform={`rotate(${tbl.rotation} ${cx} ${cy})`}
                          className={STATE_CLASS[state]}
                          strokeWidth={0.08}
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
                {floor.tables.length === 0 && <p className={`text-sm ${text.muted}`}>{h.noTables}</p>}
                {isToday && (
                  <ul className="flex flex-wrap gap-4 text-xs" aria-label={h.legend}>
                    {(["free", "upcoming", "seated"] as const).map((s) => (
                      <li key={s} className="flex items-center gap-1">
                        <span className={`inline-block h-3 w-3 rounded-sm border ${LEGEND_CLASS[s]}`} aria-hidden />
                        {h.state[s]}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>

          <div className="space-y-4">
            {listError && (
              <div className="glass-alert-error" role="alert">
                {listError}
              </div>
            )}
            {!list && !listError && <p className={text.muted}>{r.common.loading}</p>}

            {approvalsError && (
              <div className="glass-alert-error" role="alert">
                {approvalsError}
              </div>
            )}
            {approvals && approvals.length > 0 && (
              <Panel title={h.needsApproval}>
                <ul className="space-y-2">{approvals.map((res) => row(res, true))}</ul>
              </Panel>
            )}

            {list && (
              <Panel title={h.bookings}>
                {groups.length === 0 && <p className={`text-sm ${text.muted}`}>{h.noBookings}</p>}
                {groups.map((g) => (
                  <div key={g.time} className="space-y-2">
                    <h3 className={`text-sm font-semibold ${text.heading}`}>{time(g.items[0].start)}</h3>
                    <ul className="space-y-2">{g.items.map((res) => row(res))}</ul>
                  </div>
                ))}
              </Panel>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
