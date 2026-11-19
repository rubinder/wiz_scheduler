import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import { getConfig, getRules, getSlots, saveRules } from "../../api/reservations";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { DateOverride, ServicePeriod, SlotOut } from "../../types/reservations";
import {
  errorKey,
  formatLocalTime,
  localDateString,
  payloadToRulesForm,
  rulesFormToPayload,
  mockProviderOffered,
  validParty,
} from "../../utils/reservations";
import type { RulesForm } from "../../utils/reservations";
import { border, text } from "../../theme";

const EMPTY_PERIOD: ServicePeriod = { name: "", weekdays: [], first_seating: "17:00", last_seating: "21:00" };

interface FieldError {
  path: string;
  msg: string;
}

/** Pydantic 422 detail array -> "field: message" rows. Anything else -> []. */
function validationErrors(err: unknown): FieldError[] {
  if (!(err instanceof ApiError) || err.status !== 422 || !Array.isArray(err.data)) return [];
  return (err.data as { loc?: unknown[]; msg?: string }[]).map((d) => ({
    path: (d.loc ?? []).filter((p) => p !== "body").join("."),
    msg: d.msg ?? "",
  }));
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
      {hint && <span className={`block text-xs ${text.placeholder}`}>{hint}</span>}
    </label>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="glass-card p-4 space-y-3">
      <h2 className={`text-lg font-semibold ${text.heading}`}>{title}</h2>
      {children}
    </section>
  );
}

export default function ReservationsRules() {
  const { t, lang } = useLanguage();
  const { isAdmin } = useAuth();
  const r = t.reservations;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationId, setLocationId] = useState("");
  const [form, setForm] = useState<RulesForm | null>(null);
  /** JSON of the form as last loaded or saved; a different form has unsaved edits. */
  const [baseline, setBaseline] = useState("");
  const [mockAllowed, setMockAllowed] = useState(false);
  const [storedProvider, setStoredProvider] = useState("");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);

  const [partySize, setPartySize] = useState("2");
  const [date, setDate] = useState("");
  const [asDiner, setAsDiner] = useState(true);
  const [slots, setSlots] = useState<SlotOut[] | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [locationsFailed, setLocationsFailed] = useState(false);
  const currentLocation = useRef("");
  const previewSeq = useRef(0);

  const errorText = (detail: unknown): string => {
    const key = errorKey(detail).split(".")[2];
    return (r.errors as Record<string, string>)[key] ?? r.errors.generic;
  };

  const location = useMemo(() => locations.find((l) => l.id === locationId), [locations, locationId]);
  const weekdayNames = useMemo(
    // 2024-01-01 was a Monday; local-time constructor, no date-only string parsing.
    () =>
      Array.from({ length: 7 }, (_, i) =>
        new Intl.DateTimeFormat(lang, { weekday: "short" }).format(new Date(2024, 0, 1 + i)),
      ),
    [lang],
  );

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setEnabled(cfg.enabled);
        setMockAllowed(cfg.mock_provider_allowed ?? false);
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
    previewSeq.current += 1;
    if (!locationId) return;
    let cancelled = false;
    setForm(null);
    setSlots(null);
    setSaved(false);
    setSaveError("");
    setFieldErrors([]);
    setLoadError("");
    getRules(locationId)
      .then((out) => {
        if (cancelled) return;
        const loaded = payloadToRulesForm(out.rules);
        setForm(loaded);
        setBaseline(JSON.stringify(loaded));
        setStoredProvider(loaded.provider);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(errorText(e instanceof ApiError ? e.data : null));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId]);

  useEffect(() => {
    if (location) setDate(localDateString(new Date(), location.timezone));
  }, [location]);

  const dirty = form !== null && JSON.stringify(form) !== baseline;

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const patch = (p: Partial<RulesForm>) => {
    setForm((f) => (f ? { ...f, ...p } : f));
    setSaved(false);
  };
  const setList = <T,>(list: T[], i: number, v: T): T[] => list.map((x, j) => (j === i ? v : x));
  const dropAt = <T,>(list: T[], i: number): T[] => list.filter((_, j) => j !== i);
  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const removeBtn = (onClick: () => void, label?: string) => (
    <button type="button" onClick={onClick} aria-label={label} className={`text-sm ${text.muted}`}>
      {r.common.remove}
    </button>
  );
  const addBtn = (label: string, onClick: () => void) => (
    <button type="button" onClick={onClick} className="glass-btn-secondary text-sm">
      + {label}
    </button>
  );
  /** `min`/`max` mirror the server's ge/le for the field. */
  const numField = (label: string, key: keyof RulesForm, min: number, max?: number, hint?: string, step?: number) => (
    <Field label={label} hint={hint}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        className={`${input} w-full`}
        value={form ? (form[key] as string) : ""}
        onChange={(e) => patch({ [key]: e.target.value } as Partial<RulesForm>)}
      />
    </Field>
  );

  const periodEditor = (
    p: ServicePeriod,
    onChange: (p: ServicePeriod) => void,
    onRemove: () => void,
    position: number,
  ) => (
    <div className="space-y-2 rounded border p-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={r.rules.periodName}>
          <input className={input} value={p.name} onChange={(e) => onChange({ ...p, name: e.target.value })} />
        </Field>
        <Field label={r.rules.firstSeating}>
          <input
            type="time"
            className={input}
            value={p.first_seating}
            onChange={(e) => onChange({ ...p, first_seating: e.target.value })}
          />
        </Field>
        <Field label={r.rules.lastSeating}>
          <input
            type="time"
            className={input}
            value={p.last_seating}
            onChange={(e) => onChange({ ...p, last_seating: e.target.value })}
          />
        </Field>
        {removeBtn(onRemove, `${r.rules.removePeriod} ${p.name.trim() || position}`)}
      </div>
      <div className="flex flex-wrap gap-3">
        {weekdayNames.map((name, d) => (
          <label key={d} className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={p.weekdays.includes(d)}
              onChange={(e) =>
                onChange({
                  ...p,
                  weekdays: e.target.checked
                    ? [...p.weekdays, d].sort((a, b) => a - b)
                    : p.weekdays.filter((x) => x !== d),
                })
              }
            />
            {name}
          </label>
        ))}
      </div>
    </div>
  );

  const stringList = (
    items: string[],
    onChange: (items: string[]) => void,
    addLabel: string,
    type: "date" | "text",
    itemLabel: string,
  ) => (
    <div className="space-y-2">
      {items.map((v, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type={type}
            className={`${input} flex-1`}
            aria-label={`${itemLabel} ${i + 1}`}
            value={v}
            onChange={(e) => onChange(setList(items, i, e.target.value))}
          />
          {removeBtn(() => onChange(dropAt(items, i)), `${r.common.remove} ${itemLabel} ${i + 1}`)}
        </div>
      ))}
      {addBtn(addLabel, () => onChange([...items, ""]))}
    </div>
  );

  const onLocationChange = (id: string) => {
    if (dirty && !window.confirm(r.rules.discardConfirm)) return;
    setLocationId(id);
  };

  const onSave = async () => {
    if (!form) return;
    setSaving(true);
    setSaved(false);
    setSaveError("");
    setFieldErrors([]);
    const forLocation = locationId;
    try {
      const out = await saveRules(forLocation, rulesFormToPayload(form, isAdmin));
      if (currentLocation.current !== forLocation) return;
      const savedForm = payloadToRulesForm(out.rules);
      setForm(savedForm);
      setBaseline(JSON.stringify(savedForm));
      setStoredProvider(savedForm.provider);
      setSaved(true);
    } catch (e) {
      if (currentLocation.current !== forLocation) return;
      const fe = validationErrors(e);
      setFieldErrors(fe);
      setSaveError(fe.length > 0 ? r.rules.saveFailed : errorText(e instanceof ApiError ? e.data : null));
    } finally {
      setSaving(false);
    }
  };

  const onPreview = async () => {
    setPreviewError("");
    setSlots(null);
    const seq = ++previewSeq.current;
    const forLocation = locationId;
    const latest = () => seq === previewSeq.current && currentLocation.current === forLocation;
    if (!validParty(partySize)) return;
    try {
      const out = await getSlots(forLocation, Number(partySize), date, asDiner);
      if (latest()) setSlots(out);
    } catch (e) {
      if (!latest()) return;
      setPreviewError(errorText(e instanceof ApiError ? e.data : null));
    }
  };

  return (
    <div className="space-y-4">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{r.rules.title}</h1>
      <p className={`text-sm ${text.muted}`}>{r.rules.description}</p>

      {locations.length === 0 && locationsFailed ? null : locations.length === 0 ? (
        <div className="glass-card p-6 text-sm">{r.rules.noLocations}</div>
      ) : (
        <Field label={r.rules.location}>
          <select className={`${input} block`} disabled={saving} value={locationId} onChange={(e) => onLocationChange(e.target.value)}>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {loadError && (
        <div className="glass-alert-error" role="alert">
          {loadError}
        </div>
      )}
      {locationId && !form && !loadError && <p className={text.muted}>{r.common.loading}</p>}

      {form && (
        <>
          <Section title={r.rules.servicePeriods}>
            {form.service_periods.map((p, i) => (
              <div key={i}>
                {periodEditor(
                  p,
                  (np) => patch({ service_periods: setList(form.service_periods, i, np) }),
                  () => patch({ service_periods: dropAt(form.service_periods, i) }),
                  i + 1,
                )}
              </div>
            ))}
            {addBtn(r.rules.addPeriod, () =>
              patch({ service_periods: [...form.service_periods, { ...EMPTY_PERIOD }] }),
            )}
            {numField(r.rules.slotInterval, "slot_interval_minutes", 5, 60)}
          </Section>

          <Section title={r.rules.durations}>
            {form.durations.map((d, i) => (
              <div key={i} className="flex flex-wrap items-end gap-3">
                {([
                  ["max_party", 1, undefined],
                  ["minutes", 30, 360],
                  ["churn_minutes", 0, 120],
                ] as const).map(([k, min, max]) => (
                  <Field
                    key={k}
                    label={k === "max_party" ? r.rules.maxParty : k === "minutes" ? r.rules.minutes : r.rules.churnMinutes}
                  >
                    <input
                      type="number"
                      min={min}
                      max={max}
                      className={`${input} w-28`}
                      value={d[k]}
                      onChange={(e) =>
                        patch({ durations: setList(form.durations, i, { ...d, [k]: e.target.value }) })
                      }
                    />
                  </Field>
                ))}
                {removeBtn(() => patch({ durations: dropAt(form.durations, i) }), `${r.common.remove} ${r.rules.durations} ${i + 1}`)}
              </div>
            ))}
            {addBtn(r.rules.addBand, () =>
              patch({ durations: [...form.durations, { max_party: "", minutes: "", churn_minutes: "" }] }),
            )}
          </Section>

          <Section title={r.rules.maxPartySize}>
            <div className="grid gap-3 sm:grid-cols-2">
              {numField(r.rules.maxSelfBookParty, "max_self_book_party", 1)}
              {numField(r.rules.maxPartySize, "max_party_size", 1)}
              {numField(r.rules.maxConcurrent, "max_concurrent_covers", 1, undefined, r.rules.maxConcurrentHint)}
              {numField(r.rules.maxPerSlot, "max_covers_per_slot", 1, undefined, r.rules.maxPerSlotHint)}
              {numField(r.rules.horizonDays, "booking_horizon_days", 1, 365)}
              {numField(r.rules.cutoffMinutes, "cutoff_minutes", 0, 1440)}
              {numField(r.rules.holdTimeout, "hold_timeout_minutes", 1, 30)}
              {numField(r.rules.pendingHoldHours, "pending_hold_hours", 1, 72)}
            </div>
          </Section>

          <Section title={r.rules.closures}>
            {stringList(form.closures, (closures) => patch({ closures }), r.rules.addClosure, "date", r.rules.closures)}
          </Section>

          <Section title={r.rules.overrides}>
            <p className={`text-xs ${text.muted}`}>{r.rules.overrideHint}</p>
            {form.date_overrides.map((o: DateOverride, i) => {
              const setO = (no: DateOverride) => patch({ date_overrides: setList(form.date_overrides, i, no) });
              return (
                <div key={i} className="space-y-2 rounded border p-3">
                  <div className="flex flex-wrap items-end gap-3">
                    <Field label={r.rules.date}>
                      <input type="date" className={input} value={o.date} onChange={(e) => setO({ ...o, date: e.target.value })} />
                    </Field>
                    <Field label={r.rules.overrideNote}>
                      <input className={input} value={o.note} onChange={(e) => setO({ ...o, note: e.target.value })} />
                    </Field>
                    {removeBtn(() => patch({ date_overrides: dropAt(form.date_overrides, i) }), `${r.common.remove} ${r.rules.overrides} ${i + 1}`)}
                  </div>
                  {o.periods.map((p, j) => (
                    <div key={j}>
                      {periodEditor(
                        p,
                        (np) => setO({ ...o, periods: setList(o.periods, j, np) }),
                        () => setO({ ...o, periods: dropAt(o.periods, j) }),
                        j + 1,
                      )}
                    </div>
                  ))}
                  {addBtn(r.rules.addPeriod, () => setO({ ...o, periods: [...o.periods, { ...EMPTY_PERIOD }] }))}
                </div>
              );
            })}
            {addBtn(r.rules.addOverride, () =>
              patch({ date_overrides: [...form.date_overrides, { date: "", periods: [], note: "" }] }),
            )}
          </Section>

          <Section title={r.rules.persona}>
            <Field label={r.rules.voiceId}>
              <input
                className={`${input} w-full`}
                value={form.persona.voice_id}
                onChange={(e) => patch({ persona: { ...form.persona, voice_id: e.target.value } })}
              />
            </Field>
            <Field label={r.rules.greeting}>
              <textarea
                className={`${input} w-full`}
                rows={2}
                value={form.persona.greeting}
                onChange={(e) => patch({ persona: { ...form.persona, greeting: e.target.value } })}
              />
            </Field>
            <span className={`glass-label-sm ${text.muted}`}>{r.rules.facts}</span>
            {stringList(
              form.persona.facts,
              (facts) => patch({ persona: { ...form.persona, facts } }),
              r.rules.addFact,
              "text",
              r.rules.facts,
            )}
            {numField(r.rules.dailySpendCap, "daily_spend_cap_usd", 1, undefined, r.rules.dailySpendCapHint, 0.01)}
            <Field label={r.rules.transferNumber}>
              <input
                type="tel"
                className={`${input} w-full`}
                value={form.staff_transfer_number}
                onChange={(e) => patch({ staff_transfer_number: e.target.value })}
              />
            </Field>
          </Section>

          <Section title={r.rules.providerTitle}>
            <p className={`text-xs ${text.muted}`}>{r.rules.providerHint}</p>
            <Field label={r.rules.provider}>
              <select
                className={`${input} block`}
                disabled={!isAdmin}
                value={form.provider}
                onChange={(e) => patch({ provider: e.target.value as RulesForm["provider"] })}
              >
                <option value="wizscheduler">{r.rules.providerWizscheduler}</option>
                {mockProviderOffered(mockAllowed, storedProvider) && (
                  <option value="mock_external">{r.rules.providerMock}</option>
                )}
              </select>
            </Field>
            {form.provider === "mock_external" && (
              <div className="grid gap-3 sm:grid-cols-3">
                {([
                  ["max_covers_per_slot", r.rules.mockMaxCovers, 1, 500, 1],
                  ["latency_ms", r.rules.mockLatency, 0, 3000, 1],
                  ["failure_rate", r.rules.mockFailureRate, 0, 0.5, 0.05],
                ] as const).map(([k, label, min, max, step]) => (
                  <Field key={k} label={label}>
                    <input
                      type="number"
                      min={min}
                      max={max}
                      step={step}
                      disabled={!isAdmin}
                      className={`${input} w-full`}
                      value={form.mock_external[k]}
                      onChange={(e) => patch({ mock_external: { ...form.mock_external, [k]: e.target.value } })}
                    />
                  </Field>
                ))}
              </div>
            )}
          </Section>

          {saveError && (
            <div className="glass-alert-error" role="alert">
              <p>{saveError}</p>
              {fieldErrors.length > 0 && (
                <>
                  <p className="font-medium">{r.rules.validationTitle}</p>
                  <ul className="list-disc ps-5">
                    {fieldErrors.map((fe, i) => (
                      <li key={i}>
                        {fe.path ? `${fe.path}: ` : ""}
                        {fe.msg}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          {saved && (
            <div className="glass-alert-success" role="status">
              {r.common.saved}
            </div>
          )}
          <button type="button" className="glass-btn-primary" disabled={saving} onClick={onSave}>
            {saving ? r.common.saving : r.common.save}
          </button>

          <Section title={r.rules.preview}>
            <p className={`text-xs ${text.muted}`}>{r.rules.previewNote}</p>
            <div className="flex flex-wrap items-end gap-3">
              <Field label={r.rules.partySize}>
                <input
                  type="number"
                  min={1}
                  className={`${input} w-24`}
                  value={partySize}
                  onChange={(e) => setPartySize(e.target.value)}
                />
              </Field>
              <Field label={r.rules.date}>
                <input type="date" className={input} value={date} onChange={(e) => setDate(e.target.value)} />
              </Field>
              <fieldset className="flex items-center gap-3">
                <legend className="sr-only">{r.rules.preview}</legend>
                <label className="flex items-center gap-1 text-sm">
                  <input type="radio" name="audience" checked={asDiner} onChange={() => setAsDiner(true)} />
                  {r.rules.viewDiner}
                </label>
                <label className="flex items-center gap-1 text-sm">
                  <input type="radio" name="audience" checked={!asDiner} onChange={() => setAsDiner(false)} />
                  {r.rules.viewStaff}
                </label>
              </fieldset>
              <button
                type="button"
                className="glass-btn-secondary"
                onClick={onPreview}
                disabled={!date || !location || !validParty(partySize)}
              >
                {r.rules.showTimes}
              </button>
            </div>
            {previewError && (
              <div className="glass-alert-error" role="alert">
                {previewError}
              </div>
            )}
            {slots && slots.length === 0 && <p className={`text-sm ${text.muted}`}>{r.rules.noSlots}</p>}
            {slots && slots.length > 0 && location && (
              <ul className="flex flex-wrap gap-2">
                {slots.map((s) => (
                  <li key={s.start} className="glass-badge">
                    {formatLocalTime(s.start, location.timezone, lang)}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
