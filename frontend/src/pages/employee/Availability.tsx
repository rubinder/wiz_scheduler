import React, { useCallback, useEffect, useState } from "react";
import * as employeesApi from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
import { locationZoneNote } from "../../utils/timezones";
import {
  dateParts,
  overlapsExisting,
  quickSetDates,
  type QuickSetKind,
} from "../../utils/availabilityDates";
import { text, bg, border, badge } from "../../theme";
import type { EmployeeAvailability, EmployeeMeProfile } from "../../types";

export default function Availability() {
  const { t } = useLanguage();
  const [profile, setProfile] = useState<EmployeeMeProfile | null>(null);
  const [availability, setAvailability] = useState<EmployeeAvailability[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // Form state
  const [date, setDate] = useState("");
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("17:00");
  const [saving, setSaving] = useState(false);
  const [quickResult, setQuickResult] = useState("");

  const fetchProfile = useCallback(async () => {
    try {
      const data = await employeesApi.getMyProfile();
      setProfile(data);
    } catch {
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const fetchAvailability = useCallback(async () => {
    if (!profile) return;
    try {
      const data = await employeesApi.listAvailability(profile.id);
      setAvailability(data);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load availability"
      );
    }
  }, [profile]);

  useEffect(() => {
    fetchAvailability();
  }, [fetchAvailability]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile || !date) return;
    setError("");
    setQuickResult("");
    setSaving(true);

    try {
      await employeesApi.createAvailability({
        employee_id: profile.id,
        ...dateParts(date),
        start_time: `${date}T${startTime}:00`,
        end_time: `${date}T${endTime}:00`,
      });
      await fetchAvailability();
      setDate("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add availability");
    } finally {
      setSaving(false);
    }
  };

  // Add the form's hours to every matching day of the selected date's week,
  // skipping days that already have an overlapping window.
  const handleQuickSet = async (kind: QuickSetKind) => {
    if (!profile || !date) return;
    setError("");
    setQuickResult("");
    setSaving(true);
    const days = quickSetDates(date, kind);
    const toAdd = days.filter(
      (d) => !overlapsExisting(availability, d, startTime, endTime),
    );
    try {
      await Promise.all(
        toAdd.map((d) =>
          employeesApi.createAvailability({
            employee_id: profile.id,
            ...dateParts(d),
            start_time: `${d}T${startTime}:00`,
            end_time: `${d}T${endTime}:00`,
          }),
        ),
      );
      setQuickResult(
        t.availability.quickSetResult
          .replace("{added}", String(toAdd.length))
          .replace("{skipped}", String(days.length - toAdd.length)),
      );
      setDate("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add availability");
    } finally {
      await fetchAvailability();
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await employeesApi.deleteAvailability(id);
      await fetchAvailability();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to delete availability"
      );
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <h1 className={`text-2xl font-bold ${text.heading} mb-4`}>{t.availability.title}</h1>
        <p className={text.muted}>{t.common.loading}</p>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="p-6">
        <h1 className={`text-2xl font-bold ${text.heading} mb-4`}>{t.availability.title}</h1>
        <p className={text.muted}>
          {t.availability.noProfile}
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className={`text-2xl font-bold ${text.heading} mb-4`}>{t.availability.title}</h1>
      {(() => {
        // Availability is always the location's local time (#171).
        const zone = locationZoneNote(
          profile.locations.map((l) => l.timezone).filter((z): z is string => !!z),
        );
        return zone ? (
          <p className={`mb-4 text-sm ${text.muted}`}>
            {t.common.availabilityLocalTime.replace("{zone}", zone)}
          </p>
        ) : null;
      })()}

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      {(profile.roles.length > 0 || profile.locations.length > 0) && (
        <div className="glass-card p-6 mb-6">
          {profile.roles.length > 0 && (
            <div className="mb-4">
              <h2 className={`text-lg font-semibold ${text.heading} mb-3`}>{t.availability.assignedRoles}</h2>
              <div className="flex flex-wrap gap-2">
                {profile.roles.map((r) => (
                  <span
                    key={r.role_id}
                    className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${badge.role}`}
                  >
                    {r.role_name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {profile.locations.length > 0 && (
            <div>
              <h2 className={`text-lg font-semibold ${text.heading} mb-3`}>{t.availability.yourLocations}</h2>
              <div className="flex flex-wrap gap-2">
                {profile.locations.map((l) => (
                  <span
                    key={l.location_id}
                    className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${badge.location}`}
                  >
                    {l.location_name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="glass-card p-6 mb-6">
        <h2 className={`text-lg font-semibold ${text.heading} mb-4`}>{t.availability.addWindow}</h2>
        <p className={`text-sm ${text.muted} mb-4`}>
          {t.availability.addWindowDesc}
        </p>
        <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-4">
          <div>
            <label className="glass-label">
              {t.common.date}
            </label>
            <input
              type="date"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="glass-input"
            />
          </div>
          <div>
            <label className="glass-label">
              {t.common.startTime}
            </label>
            <input
              type="time"
              required
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              className="glass-input"
            />
          </div>
          <div>
            <label className="glass-label">
              {t.common.endTime}
            </label>
            <input
              type="time"
              required
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              className="glass-input"
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="glass-btn-primary disabled:opacity-50"
          >
            {saving ? t.common.saving : t.common.add}
          </button>
        </form>
        <div className="flex flex-wrap items-center gap-2 mt-4">
          <span className={`text-xs font-medium ${text.muted}`}>
            {t.availability.quickSetLabel}
          </span>
          {(
            [
              ["weekdays", t.availability.quickWeekdays],
              ["weekend", t.availability.quickWeekend],
              ["week", t.availability.quickWeek],
            ] as const
          ).map(([kind, label]) => (
            <button
              key={kind}
              type="button"
              onClick={() => handleQuickSet(kind)}
              disabled={saving || !date}
              className="glass-btn-secondary text-xs disabled:opacity-50"
            >
              {label}
            </button>
          ))}
        </div>
        {quickResult && (
          <p className={`mt-3 text-sm ${text.muted}`}>{quickResult}</p>
        )}
      </div>

      <div className="glass-card">
        <table className="glass-table">
          <thead className={bg.tableHeader}>
            <tr>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                {t.common.date}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                {t.common.start}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                {t.common.end}
              </th>
              <th className={`px-4 py-3 text-end text-xs font-medium ${text.muted} uppercase`}>
                {t.common.actions}
              </th>
            </tr>
          </thead>
          <tbody className={`divide-y ${border.divider}`}>
            {availability.map((a) => (
              <tr key={a.id} className={bg.rowHover}>
                <td className={`px-4 py-2 text-sm ${text.body}`}>
                  {a.year}-{String(a.month).padStart(2, "0")}-
                  {String(a.day).padStart(2, "0")}
                </td>
                <td className={`px-4 py-2 text-sm ${text.body}`}>{a.start_time}</td>
                <td className={`px-4 py-2 text-sm ${text.body}`}>{a.end_time}</td>
                <td className="px-4 py-2 text-end">
                  <button
                    onClick={() => handleDelete(a.id)}
                    className="text-red-400 hover:text-red-300 text-sm font-medium"
                  >
                    {t.common.delete}
                  </button>
                </td>
              </tr>
            ))}
            {availability.length === 0 && (
              <tr>
                <td
                  colSpan={4}
                  className={`px-4 py-6 text-center ${text.muted} text-sm`}
                >
                  {t.availability.noWindows}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
