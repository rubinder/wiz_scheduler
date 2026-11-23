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

/** A YYYY-MM-DD UTC day, shown without a timezone shift. */
const dayLabel = (day: string, locale: string): string =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString(locale, {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });

/**
 * "Call capacity" on the AI host billing page (admins only, decision 103):
 * the phone locations, the company's simultaneous-call allocation, the
 * busiest moment of the last 30 days, a request for more, and the staff
 * line used by every location without its own.
 */
export default function CallCapacityCard() {
  const { t, lang } = useLanguage();
  const c = t.reservations.billing.capacity;
  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;

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

  const problem = capacityRequestProblem(form, cap.max_expected_peak);
  const problemText =
    problem === "locations" ? c.errorLocations
    : problem === "peak" ? fill(c.errorPeak, { max: String(cap.max_expected_peak) })
    : problem === "note" ? c.errorNote
    : "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (problem) return;
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

  const toggle = (id: string, on: boolean) =>
    setForm((f) => ({
      ...f,
      locationIds: on ? [...f.locationIds, id] : f.locationIds.filter((x) => x !== id),
    }));

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
          <form className="space-y-3" onSubmit={submit} noValidate>
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
                  className={`${input} w-full`}
                  value={form.expectedPeak}
                  onChange={(e) => setForm({ ...form, expectedPeak: e.target.value })}
                />
              </label>
              <label className="block space-y-1 text-sm">
                <span className={text.muted}>{c.neededBy}</span>
                <input
                  type="date"
                  className={`${input} w-full`}
                  value={form.neededBy}
                  onChange={(e) => setForm({ ...form, neededBy: e.target.value })}
                />
              </label>
            </div>
            <label className="block space-y-1 text-sm">
              <span className={text.muted}>{c.note}</span>
              <textarea
                className={`${input} w-full`}
                rows={3}
                maxLength={NOTE_MAX}
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
              />
            </label>
            {problemText && form.expectedPeak !== "" && (
              <p className="text-sm text-amber-700">{problemText}</p>
            )}
            <button type="submit" className="glass-btn-primary" disabled={busy || problem !== null}>
              {c.submit}
            </button>
          </form>
        )
      )}

      <div className="space-y-1">
        <label className="block space-y-1 text-sm">
          <span className={text.muted}>{c.staffLine}</span>
          <div className="flex flex-wrap gap-2">
            <input
              type="tel"
              className={`${input} w-56`}
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
