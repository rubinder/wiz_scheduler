import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import {
  approveTimeEntries,
  attestShift,
  deriveTimeEntries,
  downloadPayrollCsv,
  listPayrollExceptions,
  listTimeEntries,
  type PayrollRange,
} from "../../api/payroll";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { Location, PayrollExceptionRow, TimeEntryRow } from "../../types";
import { formatLateness, formatPaidHours } from "../../utils/payrollHours";
import { formatTime } from "../../utils/shiftTime";

/** "YYYY-MM-DD" from a browser-local Date, without going through toISOString
 *  (which converts to UTC and can hand back yesterday). */
function isoDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** An audit timestamp (attested_at, approved_at, exported_at) as a date in
 *  the VIEWER's locale and zone.
 *
 *  Deliberately a `Date`, unlike the shift faces above it: these record when
 *  a person in this office clicked a button, so "when did I approve this"
 *  is the viewer's own question. `.slice(0, 10)` answered it with the raw
 *  UTC date, which is a day off for anyone west of UTC for the last hours of
 *  every day, and in an unreadable order for most of the 19 locales we ship.
 *  Shift times must NEVER go through here — see utils/shiftTime.ts. */
function formatAuditDate(value: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

/** The Monday-Sunday week that ended most recently.
 *
 *  The browser's local date is the right default HERE precisely because a
 *  manager picking "last week" means their own week. The backend never infers
 *  a range, so the "today is UTC" rule — which governs Python application code
 *  and tests — is not in tension with this. */
function lastCompleteWeek(): PayrollRange {
  const now = new Date();
  // getDay(): 0 = Sunday. Days since the most recent Monday.
  const sinceMonday = (now.getDay() + 6) % 7;
  const thisMonday = new Date(now);
  thisMonday.setDate(now.getDate() - sinceMonday);
  const lastMonday = new Date(thisMonday);
  lastMonday.setDate(thisMonday.getDate() - 7);
  const lastSunday = new Date(lastMonday);
  lastSunday.setDate(lastMonday.getDate() + 6);
  return { rangeStart: isoDate(lastMonday), rangeEnd: isoDate(lastSunday) };
}

export default function Payroll() {
  const { t } = useLanguage();
  const [range, setRange] = useState<PayrollRange>(lastCompleteWeek);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationId, setLocationId] = useState("");
  const [entries, setEntries] = useState<TimeEntryRow[]>([]);
  const [exceptions, setExceptions] = useState<PayrollExceptionRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [attesting, setAttesting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paidPlanOnly, setPaidPlanOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [includeExported, setIncludeExported] = useState(false);

  /** "Confirmed by Dana on 12/09/2026 — Phone battery died".
   *
   *  The reason is the whole justification for paying a shift nobody scanned
   *  into, so it travels with the attester and the date rather than being
   *  dropped on the floor as it was. Undefined (no tooltip at all) when we
   *  know neither who nor why. */
  const attestedTitle = (row: TimeEntryRow): string | undefined => {
    const who = row.attested_by_name
      ? t.payroll.attestedBy
          .replace("{name}", row.attested_by_name)
          .replace("{date}", formatAuditDate(row.attested_at))
      : "";
    const parts = [who, row.attestation_reason ?? ""].filter(Boolean);
    return parts.length ? parts.join(" \u2014 ") : undefined;
  };

  const latenessLabels = useMemo(
    () => ({
      early: t.payroll.latenessEarly,
      late: t.payroll.latenessLate,
      onTime: t.payroll.latenessOnTime,
    }),
    [t]
  );

  useEffect(() => {
    listLocations().then(setLocations).catch(() => setLocations([]));
  }, []);

  const reload = useCallback(async () => {
    // A partially typed date (e.g. mid-edit of the day field) must not fire
    // a request or show the error banner — just wait for a complete,
    // forwards range and leave whatever is already on screen alone.
    if (
      !range.rangeStart ||
      !range.rangeEnd ||
      range.rangeStart > range.rangeEnd
    ) {
      return;
    }
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      // Derive first, then read. Derivation is idempotent, so calling it on
      // every load is free after the first.
      await deriveTimeEntries(range, locationId || undefined);
      const [entriesResp, exceptionsResp] = await Promise.all([
        listTimeEntries(range, locationId || undefined),
        listPayrollExceptions(range, locationId || undefined),
      ]);
      setEntries(entriesResp.rows);
      setExceptions(exceptionsResp.rows);
      setSelected(new Set());
      setPaidPlanOnly(false);
    } catch (err) {
      // A 402 is not an error to apologise for: free-plan managers reach this
      // page only by typing the URL, and the honest answer is that this is a
      // paid feature.
      if (err instanceof ApiError && err.status === 402) {
        setPaidPlanOnly(true);
      } else {
        setError(t.payroll.loadFailed);
      }
      setEntries([]);
      setExceptions([]);
    } finally {
      setLoading(false);
    }
  }, [range, locationId, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const unexportedApproved = entries.filter(
    (e) => e.approved_at !== null && e.exported_at === null
  ).length;

  const totalMinutes = entries.reduce((sum, e) => sum + e.paid_minutes, 0);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === entries.length
        ? new Set()
        : new Set(entries.map((e) => e.id))
    );
  };

  const confirmAttest = async (shiftId: string) => {
    try {
      await attestShift(shiftId, reason.trim() || undefined);
      setAttesting(null);
      setReason("");
      await reload();
    } catch {
      setError(t.payroll.attestFailed);
      setAttesting(null);
    }
  };

  const approve = async (ids?: string[]) => {
    try {
      const result = await approveTimeEntries(
        range,
        locationId || undefined,
        ids
      );
      setNotice(
        t.payroll.approvedCount.replace("{count}", String(result.approved))
      );
      await reload();
    } catch {
      setError(t.payroll.approveFailed);
    }
  };

  const download = async () => {
    try {
      const { blob, filename } = await downloadPayrollCsv(
        range,
        locationId || undefined,
        includeExported
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      // Revoking synchronously after click() races the browser: Safari and
      // Firefox have not started reading the blob yet when the call returns,
      // and the download silently fails. A short delay is the standard
      // work-around; the URL is still released, so nothing leaks.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(t.payroll.nothingToExport);
      } else {
        setError(t.payroll.loadFailed);
      }
    }
  };

  if (paidPlanOnly) {
    return (
      <div className="p-6">
        <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
          {t.payroll.title}
        </h1>
        <p className={`mt-4 max-w-2xl ${text.muted}`}>
          {t.payroll.paidPlanOnly}
        </p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
        {t.payroll.title}
      </h1>
      <p className={`mb-6 max-w-2xl ${text.muted}`}>{t.payroll.desc}</p>

      {/* Range + location */}
      <div className="flex flex-wrap gap-4 mb-6">
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.rangeStart}
          <input
            type="date"
            value={range.rangeStart}
            onChange={(e) =>
              setRange((r) => ({ ...r, rangeStart: e.target.value }))
            }
            className="glass-input block mt-1"
          />
        </label>
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.rangeEnd}
          <input
            type="date"
            value={range.rangeEnd}
            onChange={(e) =>
              setRange((r) => ({ ...r, rangeEnd: e.target.value }))
            }
            className="glass-input block mt-1"
          />
        </label>
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.location}
          <select
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
            className="glass-input block mt-1"
          >
            <option value="">{t.payroll.allLocations}</option>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <div className="glass-alert-error mb-4">{error}</div>}
      {notice && <div className="glass-alert-success mb-4">{notice}</div>}

      {/* Exception queue */}
      <h2 className={`text-lg font-semibold mt-8 mb-1 ${text.body}`}>
        {t.payroll.exceptionsTitle}
      </h2>
      <p className={`mb-3 max-w-2xl ${text.muted}`}>
        {t.payroll.exceptionsDesc}
      </p>
      {exceptions.length === 0 ? (
        <p className={text.muted}>{t.payroll.exceptionsEmpty}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="text-start py-2">{t.payroll.columnEmployee}</th>
              <th className="text-start py-2">{t.payroll.columnDate}</th>
              <th className="text-start py-2">{t.payroll.columnLocation}</th>
              <th className="text-start py-2">{t.payroll.columnRole}</th>
              <th className="text-start py-2">{t.payroll.columnStart}</th>
              <th className="text-start py-2">{t.payroll.columnEnd}</th>
              <th className="text-end py-2">{t.payroll.columnPaidHours}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {exceptions.map((row) => (
              <tr key={row.shift_id}>
                <td className="py-1">{row.employee_name}</td>
                <td className="py-1">{row.pay_date}</td>
                <td className="py-1">{row.location_name}</td>
                <td className="py-1">{row.role_name}</td>
                {/* formatTime reads the wall-clock face off the string; a
                    Date here would show a London manager a New York 9am
                    shift as 2pm (#92). */}
                <td className="py-1">{formatTime(row.start_time)}</td>
                <td className="py-1">{formatTime(row.end_time)}</td>
                <td className="py-1 text-end">
                  {formatPaidHours(row.paid_minutes)}
                </td>
                <td className="py-1 text-end">
                  {attesting === row.shift_id ? (
                    <span className="flex gap-2 items-center justify-end">
                      <input
                        type="text"
                        value={reason}
                        maxLength={500}
                        placeholder={t.payroll.attestReason}
                        aria-label={t.payroll.attestReason}
                        onChange={(e) => setReason(e.target.value)}
                        className="glass-input"
                      />
                      <button
                        type="button"
                        className="glass-btn-primary"
                        onClick={() => void confirmAttest(row.shift_id)}
                      >
                        {t.payroll.attestConfirm}
                      </button>
                      <button
                        type="button"
                        className="glass-btn-secondary"
                        onClick={() => {
                          setAttesting(null);
                          setReason("");
                        }}
                      >
                        {t.payroll.attestCancel}
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="glass-btn-primary"
                      onClick={() => {
                        setAttesting(row.shift_id);
                        setReason("");
                      }}
                    >
                      {t.payroll.attest}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Entries */}
      <h2 className={`text-lg font-semibold mt-10 mb-3 ${text.body}`}>
        {t.payroll.entriesTitle}
      </h2>
      {entries.length === 0 ? (
        <p className={text.muted}>{t.payroll.entriesEmpty}</p>
      ) : (
        <>
          <p className={`mb-3 ${text.muted}`}>
            {t.payroll.totalHours
              .replace("{hours}", formatPaidHours(totalMinutes))
              .replace("{count}", String(entries.length))}
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="text-start py-2">
                  <input
                    type="checkbox"
                    aria-label={t.payroll.approveAll}
                    checked={
                      entries.length > 0 && selected.size === entries.length
                    }
                    onChange={toggleAll}
                  />
                </th>
                <th className="text-start py-2">{t.payroll.columnEmployee}</th>
                <th className="text-start py-2">{t.payroll.columnDate}</th>
                <th className="text-start py-2">{t.payroll.columnLocation}</th>
                <th className="text-start py-2">{t.payroll.columnRole}</th>
                <th className="text-start py-2">{t.payroll.columnStart}</th>
                <th className="text-start py-2">{t.payroll.columnEnd}</th>
                <th className="text-end py-2">{t.payroll.columnPaidHours}</th>
                <th className="text-start py-2">{t.payroll.columnSource}</th>
                <th className="text-start py-2">
                  {t.payroll.columnCheckedInAt}
                </th>
                <th className="text-start py-2">{t.payroll.columnLateness}</th>
                <th className="text-start py-2">{t.payroll.approved}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((row) => (
                <tr
                  key={row.id}
                  className={row.exported_at ? text.muted : undefined}
                >
                  <td className="py-1">
                    <input
                      type="checkbox"
                      aria-label={row.employee_name}
                      checked={selected.has(row.id)}
                      onChange={() => toggle(row.id)}
                    />
                  </td>
                  <td className="py-1">{row.employee_name}</td>
                  <td className="py-1">{row.pay_date}</td>
                  <td className="py-1">{row.location_name}</td>
                  <td className="py-1">{row.role_name}</td>
                  <td className="py-1">{formatTime(row.start_time)}</td>
                  <td className="py-1">{formatTime(row.end_time)}</td>
                  <td className="py-1 text-end">
                    {formatPaidHours(row.paid_minutes)}
                  </td>
                  <td className="py-1">
                    {row.source === "manager_attested" ? (
                      <>
                        <span title={attestedTitle(row)}>
                          {t.payroll.sourceAttested}
                        </span>
                        {/* The reason is why this row is payable at all, so
                            it belongs on the page and not only in a tooltip
                            a touch device can never open. */}
                        {row.attestation_reason && (
                          <span className={`block text-xs ${text.muted}`}>
                            {row.attestation_reason}
                          </span>
                        )}
                      </>
                    ) : (
                      t.payroll.sourceCheckedIn
                    )}
                  </td>
                  <td className="py-1">
                    {row.checked_in_at ? formatTime(row.checked_in_at) : "—"}
                  </td>
                  <td className="py-1">
                    {formatLateness(row.lateness_minutes, latenessLabels)}
                    {row.exported_at && (
                      <span className={`ms-2 ${text.muted}`}>
                        {t.payroll.alreadyExported.replace(
                          "{date}",
                          formatAuditDate(row.exported_at)
                        )}
                      </span>
                    )}
                  </td>
                  {/* Without this the Approve buttons changed nothing a
                      manager could see, so there was no way to tell approved
                      hours from unapproved ones on the page. */}
                  <td className="py-1">
                    {row.approved_at ? (
                      <span
                        aria-label={t.payroll.approved}
                        title={`${t.payroll.approved} ${formatAuditDate(
                          row.approved_at
                        )}`}
                      >
                        {"\u2713"}
                      </span>
                    ) : (
                      "\u2014"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-3 items-center mt-8">
        <button
          type="button"
          className="glass-btn-primary"
          disabled={selected.size === 0 || loading}
          onClick={() => void approve(Array.from(selected))}
        >
          {t.payroll.approveSelected}
        </button>
        <button
          type="button"
          className="glass-btn-primary"
          disabled={entries.length === 0 || loading}
          onClick={() => void approve()}
        >
          {t.payroll.approveAll}
        </button>
        <button
          type="button"
          className="glass-btn-primary"
          disabled={(unexportedApproved === 0 && !includeExported) || loading}
          onClick={() => void download()}
        >
          {t.payroll.download}
        </button>
        {/* The backend has always supported this (it is how a manager
            re-downloads a file they lost, and it never re-stamps
            exported_at); there was simply no way to ask for it. */}
        <label className={`flex items-center gap-2 text-sm ${text.muted}`}>
          <input
            type="checkbox"
            checked={includeExported}
            onChange={(e) => setIncludeExported(e.target.checked)}
          />
          {t.payroll.includeExported}
        </label>
        <span className={`text-sm ${text.muted}`}>
          {unexportedApproved === 0 && !includeExported
            ? t.payroll.nothingToExport
            : t.payroll.downloadDesc}
        </span>
      </div>
    </div>
  );
}
