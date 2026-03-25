import React, { useCallback, useEffect, useState } from "react";
import * as employeesApi from "../../api/employees";
import type { EmployeeAvailability, EmployeeMeProfile } from "../../types";

export default function Availability() {
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
        <h1 className="text-2xl font-bold mb-4">My Availability</h1>
        <p className="text-gray-500">Loading...</p>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">My Availability</h1>
        <p className="text-gray-500">
          No employee profile found for your account. Please contact your
          manager.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">My Availability</h1>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {(profile.roles.length > 0 || profile.locations.length > 0) && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          {profile.roles.length > 0 && (
            <div className="mb-4">
              <h2 className="text-lg font-semibold mb-3">Your Assigned Roles</h2>
              <div className="flex flex-wrap gap-2">
                {profile.roles.map((r) => (
                  <span
                    key={r.role_id}
                    className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-100 text-indigo-800"
                  >
                    {r.role_name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {profile.locations.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold mb-3">Your Locations</h2>
              <div className="flex flex-wrap gap-2">
                {profile.locations.map((l) => (
                  <span
                    key={l.location_id}
                    className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-green-100 text-green-800"
                  >
                    {l.location_name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4">Add Availability Window</h2>
        <p className="text-sm text-gray-500 mb-4">
          Select the days and hours you are available to work.
        </p>
        <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Date
            </label>
            <input
              type="date"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Start Time
            </label>
            <input
              type="time"
              required
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              End Time
            </label>
            <input
              type="time"
              required
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              className="border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium"
          >
            {saving ? "Saving..." : "Add"}
          </button>
        </form>
      </div>

      <div className="bg-white rounded-lg shadow">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Date
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                Start
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                End
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {availability.map((a) => (
              <tr key={a.id}>
                <td className="px-4 py-2 text-sm">
                  {a.year}-{String(a.month).padStart(2, "0")}-
                  {String(a.day).padStart(2, "0")}
                </td>
                <td className="px-4 py-2 text-sm">{a.start_time}</td>
                <td className="px-4 py-2 text-sm">{a.end_time}</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => handleDelete(a.id)}
                    className="text-red-600 hover:text-red-800 text-sm font-medium"
                  >
                    Delete
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
                  No availability windows set. Add one above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
