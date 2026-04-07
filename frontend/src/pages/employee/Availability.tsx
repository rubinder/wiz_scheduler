import React, { useCallback, useEffect, useState } from "react";
import * as employeesApi from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
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
    setSaving(true);

    const dateObj = new Date(date);
    const year = dateObj.getFullYear();
    const month = dateObj.getMonth() + 1;
    const day = dateObj.getDate();

    try {
      await employeesApi.createAvailability({
        employee_id: profile.id,
        year,
        month,
        day,
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
        <h1 className="text-2xl font-bold text-white mb-4">{t.availability.title}</h1>
        <p className="text-gray-400">{t.common.loading}</p>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold text-white mb-4">{t.availability.title}</h1>
        <p className="text-gray-400">
          {t.availability.noProfile}
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">{t.availability.title}</h1>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      {(profile.roles.length > 0 || profile.locations.length > 0) && (
        <div className="glass-card p-6 mb-6">
          {profile.roles.length > 0 && (
            <div className="mb-4">
              <h2 className="text-lg font-semibold text-white mb-3">{t.availability.assignedRoles}</h2>
              <div className="flex flex-wrap gap-2">
                {profile.roles.map((r) => (
                  <span
                    key={r.role_id}
                    className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-purple-500/15 text-purple-300 border border-purple-400/20"
                  >
                    {r.role_name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {profile.locations.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3">{t.availability.yourLocations}</h2>
              <div className="flex flex-wrap gap-2">
                {profile.locations.map((l) => (
                  <span
                    key={l.location_id}
                    className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-400/20"
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
        <h2 className="text-lg font-semibold text-white mb-4">{t.availability.addWindow}</h2>
        <p className="text-sm text-gray-400 mb-4">
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
      </div>

      <div className="glass-card">
        <table className="glass-table">
          <thead className="bg-white/[0.04]">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">
                {t.common.date}
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">
                {t.common.start}
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">
                {t.common.end}
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase">
                {t.common.actions}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.06]">
            {availability.map((a) => (
              <tr key={a.id} className="hover:bg-white/[0.03]">
                <td className="px-4 py-2 text-sm text-gray-200">
                  {a.year}-{String(a.month).padStart(2, "0")}-
                  {String(a.day).padStart(2, "0")}
                </td>
                <td className="px-4 py-2 text-sm text-gray-200">{a.start_time}</td>
                <td className="px-4 py-2 text-sm text-gray-200">{a.end_time}</td>
                <td className="px-4 py-2 text-right">
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
                  className="px-4 py-6 text-center text-gray-500 text-sm"
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
