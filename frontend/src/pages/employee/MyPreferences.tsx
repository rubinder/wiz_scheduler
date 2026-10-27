import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, errorMessage } from "../../api/client";
import {
  DISABLED_CODE,
  getMyPreferences,
  listMyCoworkers,
  submitMyPreferences,
} from "../../api/preferenceSubmissions";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type {
  Coworker,
  CoworkerRelationship,
  MySubmissionState,
  PreferenceImportance,
  PreferenceSubmissionPayload,
} from "../../types";
import {
  MAX_DAY_PREFERENCES,
  buildSubmitPayload,
  canAddDayPreference,
  draftFromState,
  nextFreeDay,
  submissionProblems,
  type SubmissionProblem,
} from "../../utils/preferenceSubmissions";

const IMPORTANCES: PreferenceImportance[] = ["low", "medium", "high"];
const RELATIONSHIPS: CoworkerRelationship[] = ["prefer_with", "prefer_apart", "never_together"];

type Section = Exclude<keyof PreferenceSubmissionPayload, "max_hours_per_week">;

function formatDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(lang);
}

export default function MyPreferences() {
  const { t, lang } = useLanguage();
  const tp = t.myPreferences;
  const [state, setState] = useState<MySubmissionState | null>(null);
  const [coworkers, setCoworkers] = useState<Coworker[]>([]);
  const [draft, setDraft] = useState<PreferenceSubmissionPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, cw] = await Promise.all([getMyPreferences(), listMyCoworkers()]);
      setState(s);
      setCoworkers(cw);
      setDraft(draftFromState(s, cw));
      setLoadError("");
    } catch (err: unknown) {
      setLoadError(
        err instanceof ApiError && err.status === 404 ? tp.noProfile : errorMessage(err, "Failed to load"),
      );
    } finally {
      setLoading(false);
    }
  }, [tp.noProfile]);

  useEffect(() => {
    void load();
  }, [load]);

  const dayNames = tp.dayNames.split(",");
  const importanceLabel: Record<PreferenceImportance, string> = {
    low: tp.importanceLow,
    medium: tp.importanceMedium,
    high: tp.importanceHigh,
  };
  const relationshipLabel: Record<CoworkerRelationship, string> = {
    prefer_with: tp.preferWith,
    prefer_apart: tp.preferApart,
    never_together: tp.neverTogether,
  };
  const problemText: Record<SubmissionProblem, string> = {
    tooManyDays: tp.problemTooManyDays.replace("{max}", String(MAX_DAY_PREFERENCES)),
    duplicateDay: tp.problemDuplicateDay,
    duplicateRange: tp.problemDuplicateRange,
    duplicateCap: tp.problemDuplicateCap,
    duplicateCoworker: tp.problemDuplicateCoworker,
    missingCoworker: tp.problemMissingCoworker,
    blackoutOrder: tp.problemBlackoutOrder,
    zeroRange: tp.problemZeroRange,
  };

  const problems = useMemo(() => (draft ? submissionProblems(draft) : []), [draft]);

  // ── draft mutation helpers ──
  const update = (fn: (d: PreferenceSubmissionPayload) => PreferenceSubmissionPayload) => {
    setSuccess("");
    setDraft((d) => (d ? fn(d) : d));
  };
  function setRow<K extends Section>(
    section: K,
    idx: number,
    patch: Partial<PreferenceSubmissionPayload[K][number]>,
  ) {
    update((d) => ({
      ...d,
      [section]: (d[section] as PreferenceSubmissionPayload[K][number][]).map((r, i) =>
        i === idx ? { ...r, ...patch } : r,
      ),
    }));
  }
  const removeRow = (section: Section, idx: number) =>
    update((d) => ({
      ...d,
      [section]: (d[section] as unknown[]).filter((_, i) => i !== idx),
    }));

  const handleSubmit = async () => {
    if (!draft || problems.length > 0) return;
    setSaving(true);
    setSubmitError("");
    setSuccess("");
    try {
      const next = await submitMyPreferences(buildSubmitPayload(draft));
      setState(next);
      setDraft(draftFromState(next, coworkers));
      setSuccess(tp.submitted);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 403 && err.data === DISABLED_CODE) {
        setSubmitError(tp.disabledError);
        setState((s) => (s ? { ...s, enabled: false } : s));
      } else if (err instanceof ApiError && err.status === 409) {
        setSubmitError(tp.conflictError);
      } else {
        setSubmitError(errorMessage(err, "Failed to send"));
      }
    } finally {
      setSaving(false);
    }
  };

  const header = (
    <div className="mb-6">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{tp.title}</h1>
      <p className={`mt-1 text-sm ${text.muted} max-w-2xl`}>{tp.description}</p>
    </div>
  );

  if (loading) {
    return (
      <div>
        {header}
        <p className={text.muted}>{t.common.loading}</p>
      </div>
    );
  }
  if (loadError || !state || !draft) {
    return (
      <div>
        {header}
        <div className="glass-alert-error">{loadError}</div>
      </div>
    );
  }
  if (!state.enabled) {
    return (
      <div>
        {header}
        <div className="glass-alert-info max-w-2xl">{tp.notEnabled}</div>
      </div>
    );
  }

  const last = state.last_decision;
  const usedTargets = new Set(draft.affinities.map((a) => a.target_employee_id));
  const nameOf = (id: string) => coworkers.find((c) => c.id === id)?.full_name ?? id;

  const importanceSelect = (value: PreferenceImportance, onChange: (v: PreferenceImportance) => void) => (
    <select
      aria-label={tp.importance}
      value={value}
      onChange={(e) => onChange(e.target.value as PreferenceImportance)}
      className="glass-input-sm"
    >
      {IMPORTANCES.map((i) => (
        <option key={i} value={i}>
          {importanceLabel[i]}
        </option>
      ))}
    </select>
  );
  const timeInput = (label: string, value: string, onChange: (v: string) => void) => (
    <input
      type="time"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="glass-input-sm"
    />
  );
  const removeButton = (section: Section, idx: number) => (
    <button
      type="button"
      onClick={() => removeRow(section, idx)}
      className="text-red-400 hover:text-red-300 text-sm font-medium"
    >
      {t.common.remove}
    </button>
  );
  const card = (title: string, hint: string | null, body: ReactNode, addButton?: ReactNode) => (
    <section className="glass-card p-5 space-y-3">
      <div>
        <h2 className={`text-lg font-semibold ${text.heading}`}>{title}</h2>
        {hint && <p className={`text-xs ${text.muted} mt-0.5`}>{hint}</p>}
      </div>
      {body}
      {addButton}
    </section>
  );
  const empty = <p className={`text-sm ${text.muted}`}>{tp.nothingYet}</p>;
  const rowClass = "flex flex-wrap items-center gap-2";
  const addClass = "glass-btn-secondary text-sm disabled:opacity-40";

  return (
    <div className="max-w-3xl">
      {header}

      {state.pending && (
        <div className="glass-alert-info mb-3">
          {tp.pendingBanner.replace("{date}", formatDate(state.pending.submitted_at, lang))}
          <div className="text-xs mt-1">{tp.replaceNote}</div>
        </div>
      )}
      {last?.status === "approved" && (
        <div className="glass-alert-success mb-3">
          {tp.approvedBanner.replace("{date}", formatDate(last.reviewed_at, lang))}
        </div>
      )}
      {last?.status === "denied" && (
        <div className="glass-alert-warning mb-3">
          {tp.deniedBanner.replace("{date}", formatDate(last.reviewed_at, lang))}
          {last.denial_reason && (
            <div className="mt-1">{tp.deniedReason.replace("{reason}", last.denial_reason)}</div>
          )}
        </div>
      )}

      <div className="space-y-4">
        {card(
          tp.maxHoursTitle,
          tp.maxHoursHint,
          <input
            type="number"
            min={0}
            max={168}
            step={0.5}
            aria-label={tp.maxHoursTitle}
            value={draft.max_hours_per_week ?? ""}
            onChange={(e) =>
              update((d) => ({
                ...d,
                max_hours_per_week: e.target.value === "" ? null : Number(e.target.value),
              }))
            }
            className="glass-input w-40"
          />,
        )}

        {card(
          tp.daysTitle,
          canAddDayPreference(draft)
            ? tp.daysHint.replace("{max}", String(MAX_DAY_PREFERENCES))
            : tp.daysLimitReached.replace("{max}", String(MAX_DAY_PREFERENCES)),
          draft.day_preferences.length === 0
            ? empty
            : draft.day_preferences.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <select
                    aria-label={tp.day}
                    value={row.day_of_week}
                    onChange={(e) => setRow("day_preferences", idx, { day_of_week: Number(e.target.value) })}
                    className="glass-input-sm"
                  >
                    {dayNames.map((name, d) => (
                      <option
                        key={d}
                        value={d}
                        disabled={d !== row.day_of_week && draft.day_preferences.some((p) => p.day_of_week === d)}
                      >
                        {name}
                      </option>
                    ))}
                  </select>
                  {importanceSelect(row.employee_importance, (v) =>
                    setRow("day_preferences", idx, { employee_importance: v }),
                  )}
                  {removeButton("day_preferences", idx)}
                </div>
              )),
          <button
            type="button"
            disabled={!canAddDayPreference(draft)}
            onClick={() =>
              update((d) => ({
                ...d,
                day_preferences: [
                  ...d.day_preferences,
                  { day_of_week: nextFreeDay(d), employee_importance: "medium" },
                ],
              }))
            }
            className={addClass}
          >
            {tp.addDay}
          </button>,
        )}

        {card(
          tp.hoursTitle,
          null,
          draft.hour_range_preferences.length === 0
            ? empty
            : draft.hour_range_preferences.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  {timeInput(t.common.startTime, row.start_time, (v) =>
                    setRow("hour_range_preferences", idx, { start_time: v }),
                  )}
                  <span className={text.muted}>–</span>
                  {timeInput(t.common.endTime, row.end_time, (v) =>
                    setRow("hour_range_preferences", idx, { end_time: v }),
                  )}
                  {importanceSelect(row.employee_importance, (v) =>
                    setRow("hour_range_preferences", idx, { employee_importance: v }),
                  )}
                  {removeButton("hour_range_preferences", idx)}
                </div>
              )),
          <button
            type="button"
            onClick={() =>
              update((d) => ({
                ...d,
                hour_range_preferences: [
                  ...d.hour_range_preferences,
                  { start_time: "09:00", end_time: "17:00", employee_importance: "medium" },
                ],
              }))
            }
            className={addClass}
          >
            {tp.addHours}
          </button>,
        )}

        {card(
          tp.capsTitle,
          tp.capsHint,
          draft.caps.length === 0
            ? empty
            : draft.caps.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  {timeInput(t.common.startTime, row.start_time, (v) => setRow("caps", idx, { start_time: v }))}
                  <span className={text.muted}>–</span>
                  {timeInput(t.common.endTime, row.end_time, (v) => setRow("caps", idx, { end_time: v }))}
                  <label className={`flex items-center gap-1 text-sm ${text.secondary}`}>
                    <input
                      type="number"
                      min={0}
                      max={168}
                      step={1}
                      value={row.max_per_week}
                      onChange={(e) => setRow("caps", idx, { max_per_week: Number(e.target.value) })}
                      className="glass-input-sm w-20"
                    />
                    {tp.maxPerWeek}
                  </label>
                  {importanceSelect(row.employee_importance, (v) =>
                    setRow("caps", idx, { employee_importance: v }),
                  )}
                  {removeButton("caps", idx)}
                </div>
              )),
          <button
            type="button"
            onClick={() =>
              update((d) => ({
                ...d,
                caps: [
                  ...d.caps,
                  { start_time: "22:00", end_time: "06:00", max_per_week: 1, employee_importance: "medium" },
                ],
              }))
            }
            className={addClass}
          >
            {tp.addCap}
          </button>,
        )}

        {card(
          tp.blackoutsTitle,
          tp.blackoutsHint,
          draft.blackouts.length === 0
            ? empty
            : draft.blackouts.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <select
                    aria-label={tp.day}
                    value={row.day_of_week}
                    onChange={(e) => setRow("blackouts", idx, { day_of_week: Number(e.target.value) })}
                    className="glass-input-sm"
                  >
                    {dayNames.map((name, d) => (
                      <option key={d} value={d}>
                        {name}
                      </option>
                    ))}
                  </select>
                  {timeInput(t.common.startTime, row.start_time, (v) => setRow("blackouts", idx, { start_time: v }))}
                  <span className={text.muted}>–</span>
                  {timeInput(t.common.endTime, row.end_time, (v) => setRow("blackouts", idx, { end_time: v }))}
                  {removeButton("blackouts", idx)}
                </div>
              )),
          <button
            type="button"
            onClick={() =>
              update((d) => ({
                ...d,
                blackouts: [...d.blackouts, { day_of_week: 0, start_time: "09:00", end_time: "12:00" }],
              }))
            }
            className={addClass}
          >
            {tp.addBlackout}
          </button>,
        )}

        {card(
          tp.coworkersTitle,
          coworkers.length === 0 ? tp.noCoworkers : tp.coworkersHint,
          draft.affinities.length === 0
            ? empty
            : draft.affinities.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <select
                    aria-label={tp.coworker}
                    value={row.target_employee_id}
                    onChange={(e) => setRow("affinities", idx, { target_employee_id: e.target.value })}
                    className="glass-input-sm"
                  >
                    <option value="">{t.common.select}</option>
                    {coworkers.map((c) => (
                      <option
                        key={c.id}
                        value={c.id}
                        disabled={c.id !== row.target_employee_id && usedTargets.has(c.id)}
                      >
                        {c.full_name}
                      </option>
                    ))}
                    {row.target_employee_id && !coworkers.some((c) => c.id === row.target_employee_id) && (
                      <option value={row.target_employee_id}>{nameOf(row.target_employee_id)}</option>
                    )}
                  </select>
                  <select
                    aria-label={tp.relationship}
                    value={row.relationship}
                    onChange={(e) =>
                      setRow("affinities", idx, { relationship: e.target.value as CoworkerRelationship })
                    }
                    className="glass-input-sm"
                  >
                    {RELATIONSHIPS.map((r) => (
                      <option key={r} value={r}>
                        {relationshipLabel[r]}
                      </option>
                    ))}
                  </select>
                  {removeButton("affinities", idx)}
                </div>
              )),
          <button
            type="button"
            disabled={coworkers.every((c) => usedTargets.has(c.id))}
            onClick={() => {
              const free = coworkers.find((c) => !usedTargets.has(c.id));
              update((d) => ({
                ...d,
                affinities: [
                  ...d.affinities,
                  { target_employee_id: free?.id ?? "", relationship: "prefer_with" },
                ],
              }));
            }}
            className={addClass}
          >
            {tp.addCoworker}
          </button>,
        )}

        {problems.length > 0 && (
          <div className="glass-alert-warning">
            <ul className="list-disc ps-5 space-y-0.5">
              {problems.map((p) => (
                <li key={p}>{problemText[p]}</li>
              ))}
            </ul>
          </div>
        )}
        {submitError && <div className="glass-alert-error">{submitError}</div>}
        {success && <div className="glass-alert-success">{success}</div>}

        <button
          type="button"
          onClick={handleSubmit}
          disabled={saving || problems.length > 0}
          className="glass-btn-primary disabled:opacity-40"
        >
          {saving ? tp.submitting : tp.submit}
        </button>
      </div>
    </div>
  );
}
