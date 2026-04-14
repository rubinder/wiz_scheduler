import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createDayBlackout,
  deleteDayBlackout,
  listAllDayBlackouts,
  listEmployees,
} from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
import { action, bg, border, text } from "../../theme";
import type { Employee, EmployeeDayBlackout } from "../../types";

// Mon=0 to match backend (Python datetime.weekday())
const DAY_NAMES = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

export default function DayBlackouts() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [blackouts, setBlackouts] = useState<EmployeeDayBlackout[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // New-blackout form state
  const [employeeId, setEmployeeId] = useState("");
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [startTime, setStartTime] = useState("20:00");
  const [endTime, setEndTime] = useState("22:00");
  const [saving, setSaving] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [emps, bos] = await Promise.all([
        listEmployees(),
        listAllDayBlackouts(),
      ]);
      setEmployees(emps);
      setBlackouts(bos);
      setError("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const empById = useMemo(() => {
    const m = new Map<string, Employee>();
    employees.forEach((e) => m.set(e.id, e));
    return m;
  }, [employees]);

  // Sort blackouts: employee name, then day, then start time
  const sortedBlackouts = useMemo(() => {
    return [...blackouts].sort((a, b) => {
      const na = empById.get(a.employee_id)?.full_name ?? "";
      const nb = empById.get(b.employee_id)?.full_name ?? "";
      if (na !== nb) return na.localeCompare(nb);
      if (a.day_of_week !== b.day_of_week) return a.day_of_week - b.day_of_week;
      return a.start_time.localeCompare(b.start_time);
    });
  }, [blackouts, empById]);

  const handleCreate = async () => {
    if (!employeeId) {
      setError(t.dayBlackouts.pickEmployee);
      return;
    }
    if (endTime <= startTime) {
      setError(t.dayBlackouts.invalidRange);
      return;
    }
    setSaving(true);
    try {
      const created = await createDayBlackout({
        employee_id: employeeId,
        day_of_week: dayOfWeek,
        start_time: startTime,
        end_time: endTime,
      });
      setBlackouts((prev) => [...prev, created]);
      setError("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteDayBlackout(id);
      setBlackouts((prev) => prev.filter((b) => b.id !== id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.dayBlackouts.title}
        </h1>
        <p className={`mt-1 text-sm ${text.muted} max-w-2xl`}>
          {t.dayBlackouts.description}
        </p>
      </div>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
          <button
            onClick={() => setError("")}
            className="ml-2 text-red-400 hover:text-red-300"
          >
            {t.common.dismiss}
          </button>
        </div>
      )}

      {/* New blackout form */}
      <div className={`${bg.addForm} rounded-xl p-6 mb-6 space-y-4`}>
        <h2 className={`text-lg font-semibold ${text.heading}`}>
          {t.dayBlackouts.addBlackout}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="glass-label-sm">{t.dayBlackouts.employee}</label>
            <select
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
              className="glass-input w-full"
            >
              <option value="">{t.common.select}</option>
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.full_name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="glass-label-sm">{t.dayBlackouts.dayOfWeek}</label>
            <select
              value={dayOfWeek}
              onChange={(e) => setDayOfWeek(Number(e.target.value))}
              className="glass-input w-full"
            >
              {DAY_NAMES.map((name, idx) => (
                <option key={idx} value={idx}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="glass-label-sm">{t.common.startTime}</label>
            <input
              type="time"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          <div>
            <label className="glass-label-sm">{t.common.endTime}</label>
            <input
              type="time"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              className="glass-input w-full"
            />
          </div>
        </div>
        <div>
          <button
            onClick={handleCreate}
            disabled={saving || !employeeId}
            className="glass-btn-success disabled:opacity-40"
          >
            {saving ? (t.common.saving ?? "Saving...") : t.dayBlackouts.addBlackout}
          </button>
        </div>
      </div>

      {/* Existing blackouts list */}
      <div className="glass-card overflow-hidden">
        <table className="w-full">
          <thead className={`${bg.tableHeader} border-b ${border.default}`}>
            <tr>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.dayBlackouts.employee}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.dayBlackouts.dayOfWeek}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.dayBlackouts.timeRange}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.common.actions ?? "Actions"}
              </th>
            </tr>
          </thead>
          <tbody className={`divide-y ${border.divider}`}>
            {loading && (
              <tr>
                <td colSpan={4} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.common.loading ?? "Loading..."}
                </td>
              </tr>
            )}
            {!loading && sortedBlackouts.length === 0 && (
              <tr>
                <td colSpan={4} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.dayBlackouts.noBlackouts}
                </td>
              </tr>
            )}
            {sortedBlackouts.map((bo) => {
              const empName =
                empById.get(bo.employee_id)?.full_name ?? bo.employee_id;
              const dayName = DAY_NAMES[bo.day_of_week] ?? `Day ${bo.day_of_week}`;
              return (
                <tr key={bo.id} className={bg.rowHover}>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>
                    {empName}
                  </td>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>
                    {dayName}
                  </td>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>
                    {bo.start_time} – {bo.end_time}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleDelete(bo.id)}
                      className={action.delete}
                    >
                      {t.common.delete}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
