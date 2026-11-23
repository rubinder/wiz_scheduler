import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import * as capacityApi from "../../api/voiceCapacity";
import type { VoiceCapacity } from "../../api/voiceCapacity";
import { useLanguage } from "../../i18n/LanguageContext";
import { border, text } from "../../theme";
import {
  NOTE_MAX,
  capacityRequestBody,
  capacityRequestProblem,
  emptyCapacityForm,
  transferNumberBody,
  transferNumberProblem,
} from "../../utils/voiceCapacity";
import type { CapacityForm } from "../../utils/voiceCapacity";

const fill = (template: string, values: Record<string, string>): string =>
  template.replace(/\{(\w+)\}/g, (m, k: string) => values[k] ?? m);

const inputClass = `glass-input-sm text-sm ${border.default} ${text.primary}`;

/** A YYYY-MM-DD UTC day, shown without a timezone shift. */
const dayLabel = (day: string, locale: string): string =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString(locale, {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });

interface FieldsProps {
  cap: VoiceCapacity;
  form: CapacityForm;
  busy: boolean;
  onChange: (form: CapacityForm) => void;
  onSubmit: () => void;
}

/** The "Request more capacity" form: fields, the first problem, and Send. */
export function CapacityRequestFields({ cap, form, busy, onChange, onSubmit }: FieldsProps) {
  const c = useLanguage().t.reservations.billing.capacity;
  const problem = capacityRequestProblem(form, cap.max_expected_peak);
  const problemText =
    problem === "locations" ? c.errorLocations
    : problem === "peak" ? fill(c.errorPeak, { max: String(cap.max_expected_peak) })
    : problem === "note" ? c.errorNote
    : "";
  const toggle = (id: string, on: boolean) =>
    onChange({
      ...form,
      locationIds: on ? [...form.locationIds, id] : form.locationIds.filter((x) => x !== id),
    });

  return (
    <form
      className="space-y-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem) onSubmit();
      }}
    >
      <h3 className={`font-semibold ${text.heading}`}>{c.requestTitle}</h3>
      <fieldset className="space-y-1">
        <legend className={`text-sm ${text.muted}`}>{c.locationsLabel}</legend>
        {cap.voice_locations.map((l) => (
          <label key={l.location_id} className={`flex items-center gap-2 text-sm ${text.primary}`}>
            <input
              type="checkbox"
              checked={form.locationIds.includes(l.location_id)}
              onChange={(e) => toggle(l.location_id, e.target.checked)}
            />
            {l.name}
          </label>
        ))}
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-sm">
          <span className={text.muted}>{c.expectedPeak}</span>
          <input
            type="number"
            min={1}
            max={cap.max_expected_peak}
            className={`${inputClass} w-full`}
            value={form.expectedPeak}
            onChange={(e) => onChange({ ...form, expectedPeak: e.target.value })}
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className={text.muted}>{c.neededBy}</span>
          <input
            type="date"
            className={`${inputClass} w-full`}
            value={form.neededBy}
            onChange={(e) => onChange({ ...form, neededBy: e.target.value })}
          />
        </label>
      </div>
      <label className="block space-y-1 text-sm">
        <span className={text.muted}>{c.note}</span>
        <textarea
          className={`${inputClass} w-full`}
          rows={3}
          maxLength={NOTE_MAX}
          value={form.note}
          onChange={(e) => onChange({ ...form, note: e.target.value })}
        />
      </label>
      {problemText && form.expectedPeak !== "" && <p className="text-sm text-amber-700">{problemText}</p>}
      <button type="submit" className="glass-btn-primary" disabled={busy || problem !== null}>
        {c.submit}
      </button>
    </form>
  );
}

/**
 * "Call capacity" on the AI host billing page (admins only, decision 103):
 * the phone locations, the company's simultaneous-call allocation, the
 * busiest moment of the last 30 days, a request for more, and the staff
 * line used by every location without its own. Nothing while voice is off
 * (the routes 404 then).
 */
export default function CallCapacityCard({ voiceEnabled }: { voiceEnabled: boolean }) {
  if (!voiceEnabled) return null;
  return <CallCapacity />;
}

function CallCapacity() {
  const { t, lang } = useLanguage();
  const c = t.reservations.billing.capacity;

  const [cap, setCap] = useState<VoiceCapacity | null>(null);
  const [form, setForm] = useState<CapacityForm>(emptyCapacityForm());
  const [staffLine, setStaffLine] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const show = (v: VoiceCapacity) => {
    setCap(v);
    setStaffLine(v.default_staff_transfer_number ?? "");
    setForm(emptyCapacityForm(v.voice_locations.map((l) => l.location_id)));
  };

  useEffect(() => {
    capacityApi.getVoiceCapacity().then(show).catch(() => setError(c.loadFailed));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!cap) return error ? <div className="glass-alert-error" role="alert">{error}</div> : null;

  const submit = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      show(await capacityApi.requestVoiceCapacity(capacityRequestBody(form)));
      setNotice(c.sent);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? c.alreadyPending : c.failed);
    } finally {
      setBusy(false);
    }
  };

  const saveStaffLine = async () => {
    if (transferNumberProblem(staffLine)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      show(await capacityApi.saveDefaultTransferNumber(transferNumberBody(staffLine)));
      setNotice(c.staffLineSaved);
    } catch {
      setError(c.staffLineFailed);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="glass-card p-4 space-y-4">
      <div className="space-y-1">
        <h2 className={`text-lg font-semibold ${text.heading}`}>{c.title}</h2>
        <p className={`text-sm ${text.secondary}`}>{c.intro}</p>
      </div>

      {notice && <div className="glass-alert-success" role="status">{notice}</div>}
      {error && <div className="glass-alert-error" role="alert">{error}</div>}

      <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div>
          <dt className={text.muted}>{c.voiceLocations}</dt>
          <dd className={text.primary}>
            {cap.voice_locations.length === 0
              ? c.noVoiceLocations
              : cap.voice_locations.map((l) => l.name).join(", ")}
          </dd>
        </div>
        <div>
          <dt className={text.muted}>{c.allocation}</dt>
          <dd className={text.primary}>{cap.allocation}</dd>
        </div>
        <div>
          <dt className={text.muted}>{c.busiest}</dt>
          <dd className={text.primary}>
            {cap.busiest
              ? fill(c.busiestValue, {
                  n: String(cap.busiest.peak_concurrent),
                  date: dayLabel(cap.busiest.day, lang),
                })
              : c.noCalls}
          </dd>
        </div>
      </dl>

      {cap.pending_request ? (
        <p className={`text-sm ${text.secondary}`}>
          {fill(c.pending, { n: String(cap.pending_request.expected_peak) })}
        </p>
      ) : (
        cap.voice_locations.length > 0 && (
          <CapacityRequestFields cap={cap} form={form} busy={busy} onChange={setForm} onSubmit={submit} />
        )
      )}

      <div className="space-y-1">
        <label className="block space-y-1 text-sm">
          <span className={text.muted}>{c.staffLine}</span>
          <div className="flex flex-wrap gap-2">
            <input
              type="tel"
              className={`${inputClass} w-56`}
              placeholder="+12125550123"
              value={staffLine}
              onChange={(e) => setStaffLine(e.target.value)}
            />
            <button
              type="button"
              className="glass-btn-secondary"
              disabled={busy || transferNumberProblem(staffLine)}
              onClick={saveStaffLine}
            >
              {c.staffLineSave}
            </button>
          </div>
        </label>
        <p className={`text-xs ${text.muted}`}>
          {transferNumberProblem(staffLine) ? c.staffLineInvalid : c.staffLineHint}
        </p>
      </div>
    </section>
  );
}
