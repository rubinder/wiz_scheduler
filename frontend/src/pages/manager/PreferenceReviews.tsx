import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, errorMessage } from "../../api/client";
import { getCompany } from "../../api/company";
import { listEmployees } from "../../api/employees";
import { listLocations } from "../../api/locations";
import {
  ALREADY_DECIDED_CODE,
  approveSubmission,
  denySubmission,
  getSubmission,
  listSubmissions,
} from "../../api/preferenceSubmissions";
import { useLanguage } from "../../i18n/LanguageContext";
import { bg, border, text } from "../../theme";
import type {
  ApprovedPayload,
  CoworkerRelationship,
  PreferenceImportance,
  PreferenceReviewListItem,
  PreferenceSubmissionDetail,
} from "../../types";
import {
  AFFINITY_LEVELS,
  buildApprovePayload,
  buildReviewDraft,
  hasMissingWeight,
  outOfScopeCoworkerId,
} from "../../utils/preferenceSubmissions";

type Section = Exclude<keyof ApprovedPayload, "max_hours_per_week">;

function formatDateTime(iso: string, lang: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
}

export default function PreferenceReviews() {
  const { t, lang } = useLanguage();
  const tr = t.preferenceReviews;
  const tp = t.myPreferences;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [queue, setQueue] = useState<PreferenceReviewListItem[]>([]);
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const [locationNames, setLocationNames] = useState<Map<string, string>>(new Map());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [flash, setFlash] = useState("");

  const [detail, setDetail] = useState<PreferenceSubmissionDetail | null>(null);
  const [draft, setDraft] = useState<ApprovedPayload | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [actionError, setActionError] = useState("");

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const [company, items] = await Promise.all([getCompany(), listSubmissions()]);
      setEnabled(!!company.employee_preference_submissions_enabled);
      setQueue(items);
      setLoadError("");
    } catch (err: unknown) {
      setLoadError(errorMessage(err, "Failed to load"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadQueue();
    // Names only label rows; a failure here leaves ids visible.
    listEmployees()
      .then((emps) => setNames(new Map(emps.map((e) => [e.id, e.full_name]))))
      .catch(() => undefined);
    listLocations()
      .then((locs) => setLocationNames(new Map(locs.map((l) => [l.id, l.name]))))
      .catch(() => undefined);
  }, [loadQueue]);

  const describeError = (err: unknown): string => {
    if (err instanceof ApiError) {
      const data = err.data as { code?: string } | undefined;
      if (err.status === 409 && data?.code === ALREADY_DECIDED_CODE) return tr.alreadyDecided;
      if (err.status === 404) return tr.notFound;
      const coworkerId = outOfScopeCoworkerId(err.status, err.data);
      if (coworkerId) return tr.coworkerOutOfScope.replace("{name}", coworkerName(coworkerId));
    }
    return errorMessage(err, "Request failed");
  };

  const openReview = async (id: string) => {
    setFlash("");
    setActionError("");
    setReason("");
    try {
      const d = await getSubmission(id);
      setDetail(d);
      setDraft(buildReviewDraft(d));
    } catch (err: unknown) {
      setLoadError(describeError(err));
    }
  };

  const closeReview = () => {
    setDetail(null);
    setDraft(null);
    setActionError("");
  };

  const decide = async (kind: "approve" | "deny") => {
    if (!detail || !draft) return;
    setBusy(kind);
    setActionError("");
    try {
      if (kind === "approve") {
        await approveSubmission(detail.id, buildApprovePayload(draft));
      } else {
        await denySubmission(detail.id, reason.trim() || null);
      }
      setQueue((q) => q.filter((s) => s.id !== detail.id));
      closeReview();
      setFlash(kind === "approve" ? tr.approvedMessage : tr.deniedMessage);
    } catch (err: unknown) {
      setActionError(describeError(err));
      // Only a decided/replaced (409) or vanished (404) submission leaves the
      // queue; a 422, such as an out-of-scope association, stays to be fixed
      // or denied.
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        setQueue((q) => q.filter((s) => s.id !== detail.id));
      }
    } finally {
      setBusy(null);
    }
  };

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
    neutral: tp.neutral,
  };
  const levelLabel = (v: number): string => {
    if (v === 1) return t.association.levelMustTogether;
    if (v === 0.5) return t.association.levelPreferTogether;
    if (v === 0) return t.association.levelNeutral;
    if (v === -0.5) return t.association.levelPreferApart;
    if (v === -1) return t.association.levelMustNotTogether;
    return String(v);
  };

  const coworkerName = useMemo(() => {
    const fromDetail = new Map(
      (detail?.current.affinities ?? []).map((a) => [a.target_employee_id, a.target_name]),
    );
    return (id: string) => fromDetail.get(id) ?? names.get(id) ?? tr.unknownCoworker;
  }, [detail, names, tr.unknownCoworker]);

  const header = (
    <div className="mb-6">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{tr.title}</h1>
      <p className={`mt-1 text-sm ${text.muted} max-w-3xl`}>{tr.description}</p>
    </div>
  );

  if (loading && enabled === null) {
    return (
      <div>
        {header}
        <p className={text.muted}>{t.common.loading}</p>
      </div>
    );
  }

  if (enabled === false) {
    return (
      <div>
        {header}
        {loadError && <div className="glass-alert-error mb-4">{loadError}</div>}
        <div className="glass-alert-info max-w-2xl">{tr.notEnabled}</div>
      </div>
    );
  }

  // ── Review panel helpers ──
  const update = (fn: (d: ApprovedPayload) => ApprovedPayload) => setDraft((d) => (d ? fn(d) : d));
  function setRow<K extends Section>(section: K, idx: number, patch: Partial<ApprovedPayload[K][number]>) {
    update((d) => ({
      ...d,
      [section]: (d[section] as ApprovedPayload[K][number][]).map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    }));
  }
  const removeRow = (section: Section, idx: number) =>
    update((d) => ({ ...d, [section]: (d[section] as unknown[]).filter((_, i) => i !== idx) }));

  const rowClass = "flex flex-wrap items-center gap-2";
  const muted = `text-sm ${text.muted}`;
  const none = <p className={muted}>{tr.none}</p>;
  const removeButton = (section: Section, idx: number) => (
    <button
      type="button"
      onClick={() => removeRow(section, idx)}
      className="text-red-400 hover:text-red-300 text-sm font-medium"
    >
      {t.common.remove}
    </button>
  );
  const weightInput = (value: number, onChange: (v: number) => void) => (
    <label className={`flex items-center gap-1 text-sm ${text.secondary}`}>
      {tr.weight}
      <input
        type="number"
        min={0}
        max={1}
        step={0.1}
        value={Number.isNaN(value) ? "" : value}
        onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))}
        className="glass-input-sm w-20"
      />
    </label>
  );
  const askedHint = (imp: PreferenceImportance | null | undefined) =>
    imp ? (
      <span className={`text-xs ${text.muted}`}>{tr.employeeAsked.replace("{importance}", importanceLabel[imp])}</span>
    ) : null;
  const timeInput = (label: string, value: string, onChange: (v: string) => void) => (
    <input
      type="time"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="glass-input-sm"
    />
  );
  const range = (s: string, e: string) => `${s}–${e}`;
  const compare = (title: string, current: ReactNode, requested: ReactNode) => (
    <section className={`border-t ${border.default} pt-4`}>
      <h3 className={`text-sm font-semibold uppercase tracking-wide ${text.muted} mb-2`}>{title}</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className={`${bg.sectionSubtle} rounded-lg p-3 space-y-1`}>{current}</div>
        <div className="space-y-2">{requested}</div>
      </div>
    </section>
  );
  const list = (items: string[]) =>
    items.length === 0 ? (
      none
    ) : (
      <ul className={`text-sm ${text.primary} space-y-0.5`}>
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    );

  const renderReview = (d: PreferenceSubmissionDetail, dr: ApprovedPayload) => {
    const cur = d.current;
    const weightMissing = hasMissingWeight(dr);
    return (
      <div className="glass-card p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className={`text-lg font-semibold ${text.heading}`}>
            {tr.reviewing.replace("{name}", d.employee.full_name)}
          </h2>
          <button type="button" onClick={closeReview} className="glass-btn-secondary text-sm">
            {tr.backToQueue}
          </button>
        </div>
        <div className={`hidden md:grid grid-cols-2 gap-4 text-xs font-semibold uppercase ${text.muted}`}>
          <div>{tr.currentColumn}</div>
          <div>{tr.requestedColumn}</div>
        </div>

        {compare(
          tr.maxHours,
          <p className={`text-sm ${text.primary}`}>{cur.max_hours_per_week ?? tr.noLimit}</p>,
          <input
            type="number"
            min={0}
            max={168}
            step={0.5}
            aria-label={tr.maxHours}
            placeholder={tr.noLimit}
            value={dr.max_hours_per_week ?? ""}
            onChange={(e) =>
              update((x) => ({
                ...x,
                max_hours_per_week: e.target.value === "" ? null : Number(e.target.value),
              }))
            }
            className="glass-input-sm w-32"
          />,
        )}

        {compare(
          tr.days,
          list(cur.day_preferences.map((p) => `${dayNames[p.day_of_week]} · ${tr.weight} ${p.weight}`)),
          dr.day_preferences.length === 0
            ? none
            : dr.day_preferences.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <select
                    aria-label={tp.day}
                    value={row.day_of_week}
                    onChange={(e) => setRow("day_preferences", idx, { day_of_week: Number(e.target.value) })}
                    className="glass-input-sm"
                  >
                    {dayNames.map((name, i) => (
                      <option key={i} value={i}>
                        {name}
                      </option>
                    ))}
                  </select>
                  {weightInput(row.weight, (v) => setRow("day_preferences", idx, { weight: v }))}
                  {askedHint(row.employee_importance)}
                  {removeButton("day_preferences", idx)}
                </div>
              )),
        )}

        {compare(
          tr.hours,
          list(cur.hour_range_preferences.map((p) => `${range(p.start_time, p.end_time)} · ${tr.weight} ${p.weight}`)),
          dr.hour_range_preferences.length === 0
            ? none
            : dr.hour_range_preferences.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  {timeInput(t.common.startTime, row.start_time, (v) =>
                    setRow("hour_range_preferences", idx, { start_time: v }),
                  )}
                  <span className={text.muted}>–</span>
                  {timeInput(t.common.endTime, row.end_time, (v) =>
                    setRow("hour_range_preferences", idx, { end_time: v }),
                  )}
                  {weightInput(row.weight, (v) => setRow("hour_range_preferences", idx, { weight: v }))}
                  {askedHint(row.employee_importance)}
                  {removeButton("hour_range_preferences", idx)}
                </div>
              )),
        )}

        {compare(
          tr.caps,
          list(
            cur.caps.map(
              (p) =>
                `${range(p.start_time, p.end_time)} · ${tr.capRow.replace("{n}", String(p.max_per_week))} · ${tr.weight} ${p.weight}`,
            ),
          ),
          dr.caps.length === 0
            ? none
            : dr.caps.map((row, idx) => (
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
                  {weightInput(row.weight, (v) => setRow("caps", idx, { weight: v }))}
                  {askedHint(row.employee_importance)}
                  {removeButton("caps", idx)}
                </div>
              )),
        )}

        {compare(
          tr.blackouts,
          list(cur.blackouts.map((b) => `${dayNames[b.day_of_week]} ${range(b.start_time, b.end_time)}`)),
          dr.blackouts.length === 0
            ? none
            : dr.blackouts.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <select
                    aria-label={tp.day}
                    value={row.day_of_week}
                    onChange={(e) => setRow("blackouts", idx, { day_of_week: Number(e.target.value) })}
                    className="glass-input-sm"
                  >
                    {dayNames.map((name, i) => (
                      <option key={i} value={i}>
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
        )}

        {compare(
          tr.coworkers,
          list(cur.affinities.map((a) => `${a.target_name} · ${levelLabel(a.level)}`)),
          dr.affinities.length === 0
            ? none
            : dr.affinities.map((row, idx) => (
                <div key={idx} className={rowClass}>
                  <span className={`text-sm font-medium ${text.primary} min-w-[8rem]`}>
                    {coworkerName(row.target_employee_id)}
                  </span>
                  <select
                    aria-label={tr.level}
                    value={row.level}
                    onChange={(e) => setRow("affinities", idx, { level: Number(e.target.value) })}
                    className="glass-input-sm"
                  >
                    {AFFINITY_LEVELS.map((v) => (
                      <option key={v} value={v}>
                        {levelLabel(v)}
                      </option>
                    ))}
                  </select>
                  <span className={`text-xs ${text.muted}`}>
                    {tr.employeeAskedRelationship.replace("{relationship}", relationshipLabel[row.relationship])}
                  </span>
                  {removeButton("affinities", idx)}
                </div>
              )),
        )}

        <div className={`border-t ${border.default} pt-4 space-y-3`}>
          <label className="glass-label-sm" htmlFor="deny-reason">
            {tr.denyReason}
          </label>
          <textarea
            id="deny-reason"
            maxLength={500}
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="glass-input w-full"
          />
          {actionError && <div className="glass-alert-error">{actionError}</div>}
          {weightMissing && <div className="glass-alert-error">{tr.weightRequired}</div>}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={busy !== null || weightMissing}
              onClick={() => decide("approve")}
              className="glass-btn-success disabled:opacity-40"
            >
              {busy === "approve" ? tr.approving : tr.approve}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => decide("deny")}
              className="glass-btn-danger disabled:opacity-40"
            >
              {busy === "deny" ? tr.denying : tr.deny}
            </button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div>
      {header}
      {loadError && <div className="glass-alert-error mb-4">{loadError}</div>}
      {flash && <div className="glass-alert-success mb-4">{flash}</div>}

      {detail && draft ? (
        renderReview(detail, draft)
      ) : (
        <div className="glass-card overflow-hidden">
          <table className="w-full">
            <thead className={`${bg.tableHeader} border-b ${border.default}`}>
              <tr>
                {[tr.employee, tr.locations, tr.submittedAt, ""].map((h, i) => (
                  <th
                    key={i}
                    className={`text-start px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className={`divide-y ${border.divider}`}>
              {queue.length === 0 && (
                <tr>
                  <td colSpan={4} className={`px-4 py-6 text-sm ${text.muted}`}>
                    {loading ? t.common.loading : tr.queueEmpty}
                  </td>
                </tr>
              )}
              {queue.map((item) => (
                <tr key={item.id} className={bg.rowHover}>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>{item.employee_name}</td>
                  <td className={`px-4 py-3 text-sm ${text.secondary}`}>
                    {item.location_ids.map((id) => locationNames.get(id) ?? id).join(", ")}
                  </td>
                  <td className={`px-4 py-3 text-sm ${text.secondary}`}>
                    {formatDateTime(item.submitted_at, lang)}
                  </td>
                  <td className="px-4 py-3 text-end">
                    <button type="button" onClick={() => openReview(item.id)} className="glass-btn-primary text-sm">
                      {tr.review}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
