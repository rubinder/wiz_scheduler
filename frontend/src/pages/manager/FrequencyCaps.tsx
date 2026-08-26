import { useCallback, useEffect, useMemo, useState } from "react";
import { errorMessage } from "../../api/client";
import { listEmployees } from "../../api/employees";
import {
  createHourRangeCap,
  deleteHourRangeCap,
  listHourRangeCaps,
  updateHourRangeCap,
} from "../../api/schedulingPreferences";
import WeightSlider from "../../components/shared/WeightSlider";
import { useLanguage } from "../../i18n/LanguageContext";
import { action, bg, border, text } from "../../theme";
import type { Employee, EmployeeHourRangeCap } from "../../types";

interface DraftRow {
  id: string;
  employee_id: string;
  start_time: string;
  end_time: string;
  max_per_week: string; // input state as string so parsing/validation is explicit
  weight: number;
  originalMaxPerWeek: number;
  originalWeight: number;
  saving: boolean;
  error: string | null;
}

function toDraft(cap: EmployeeHourRangeCap): DraftRow {
  return {
    id: cap.id,
    employee_id: cap.employee_id,
    start_time: cap.start_time,
    end_time: cap.end_time,
    max_per_week: String(cap.max_per_week),
    weight: cap.weight,
    originalMaxPerWeek: cap.max_per_week,
    originalWeight: cap.weight,
    saving: false,
    error: null,
  };
}

export default function FrequencyCaps() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");

  // New-cap form state
  const [employeeId, setEmployeeId] = useState("");
  const [startTime, setStartTime] = useState("16:00");
  const [endTime, setEndTime] = useState("22:00");
  const [maxPerWeek, setMaxPerWeek] = useState("3");
  const [newWeight, setNewWeight] = useState(0.7);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [emps, caps] = await Promise.all([
        listEmployees(),
        listHourRangeCaps(),
      ]);
      setEmployees(emps);
      setRows(caps.map(toDraft));
      setLoadError("");
    } catch (err: unknown) {
      setLoadError(errorMessage(err, "Failed to load"));
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

  const filteredRows = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const sorted = [...rows].sort((a, b) => {
      const na = empById.get(a.employee_id)?.full_name ?? "";
      const nb = empById.get(b.employee_id)?.full_name ?? "";
      if (na !== nb) return na.localeCompare(nb);
      return a.start_time.localeCompare(b.start_time);
    });
    if (!f) return sorted;
    return sorted.filter((r) =>
      (empById.get(r.employee_id)?.full_name ?? "").toLowerCase().includes(f)
    );
  }, [rows, filter, empById]);

  const handleCreate = async () => {
    if (!employeeId) {
      setCreateError(t.frequencyCaps.pickEmployee);
      return;
    }
    if (startTime === endTime) {
      setCreateError(t.frequencyCaps.invalidRange);
      return;
    }
    const parsedMax = Number(maxPerWeek);
    if (!Number.isFinite(parsedMax) || parsedMax < 0) {
      setCreateError(t.frequencyCaps.invalidMax);
      return;
    }
    setCreating(true);
    try {
      const created = await createHourRangeCap({
        employee_id: employeeId,
        start_time: startTime,
        end_time: endTime,
        max_per_week: parsedMax,
        weight: newWeight,
      });
      setRows((prev) => [...prev, toDraft(created)]);
      setCreateError("");
      setNewWeight(0.7);
    } catch (err: unknown) {
      setCreateError(errorMessage(err, "Save failed"));
    } finally {
      setCreating(false);
    }
  };

  const handleWeightChange = (id: string, weight: number) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, weight, error: null } : r))
    );
  };

  const handleMaxChange = (id: string, value: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, max_per_week: value, error: null } : r))
    );
  };

  const isDirty = (row: DraftRow): boolean => {
    const parsed = Number(row.max_per_week);
    if (!Number.isFinite(parsed)) return true;
    return parsed !== row.originalMaxPerWeek || row.weight !== row.originalWeight;
  };

  const handleSave = async (id: string) => {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    const parsedMax = Number(row.max_per_week);
    if (!Number.isFinite(parsedMax) || parsedMax < 0) {
      setRows((prev) =>
        prev.map((r) =>
          r.id === id ? { ...r, error: t.frequencyCaps.invalidMax } : r
        )
      );
      return;
    }
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, saving: true, error: null } : r))
    );
    try {
      const updated = await updateHourRangeCap(id, {
        max_per_week: parsedMax,
        weight: row.weight,
      });
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? {
                ...r,
                max_per_week: String(updated.max_per_week),
                weight: updated.weight,
                originalMaxPerWeek: updated.max_per_week,
                originalWeight: updated.weight,
                saving: false,
                error: null,
              }
            : r
        )
      );
    } catch (err: unknown) {
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? { ...r, saving: false, error: errorMessage(err, "Save failed") }
            : r
        )
      );
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteHourRangeCap(id);
      setRows((prev) => prev.filter((r) => r.id !== id));
    } catch (err: unknown) {
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? { ...r, error: errorMessage(err, "Delete failed") }
            : r
        )
      );
    }
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.frequencyCaps.title}
        </h1>
        <p className={`mt-1 text-sm ${text.muted} max-w-2xl`}>
          {t.frequencyCaps.description}
        </p>
        <p className={`mt-1 text-sm ${text.muted} max-w-2xl`}>
          {t.frequencyCaps.matchRule}
        </p>
      </div>

      {loadError && (
        <div className="glass-alert-error mb-4">
          {loadError}
          <button
            onClick={() => setLoadError("")}
            className="ml-2 text-red-400 hover:text-red-300"
          >
            {t.common.dismiss}
          </button>
        </div>
      )}

      {/* New-cap form */}
      <div className={`${bg.addForm} rounded-xl p-6 mb-6 space-y-4`}>
        <h2 className={`text-lg font-semibold ${text.heading}`}>
          {t.frequencyCaps.addCap}
        </h2>
        {createError && (
          <div className="glass-alert-error">
            {createError}
            <button
              onClick={() => setCreateError("")}
              className="ml-2 text-red-400 hover:text-red-300"
            >
              {t.common.dismiss}
            </button>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div>
            <label className="glass-label-sm">{t.frequencyCaps.employee}</label>
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
          <div>
            <label className="glass-label-sm">{t.frequencyCaps.maxPerWeek}</label>
            <input
              type="number"
              min={0}
              step={1}
              value={maxPerWeek}
              onChange={(e) => setMaxPerWeek(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          <div>
            <label className="glass-label-sm">{t.frequencyCaps.weight}</label>
            <WeightSlider
              value={newWeight}
              onChange={setNewWeight}
              label=""
              hardWarning={t.frequencyCaps.hardWarning}
            />
          </div>
        </div>
        <div>
          <button
            onClick={handleCreate}
            disabled={creating || !employeeId}
            className="glass-btn-success disabled:opacity-40"
          >
            {creating ? (t.common.saving ?? "Saving...") : t.frequencyCaps.addCap}
          </button>
        </div>
      </div>

      <div className="mb-4 max-w-sm">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t.frequencyCaps.searchPlaceholder}
          className="glass-input w-full"
        />
      </div>

      <div className="glass-card overflow-hidden">
        <table className="w-full">
          <thead className={`${bg.tableHeader} border-b ${border.default}`}>
            <tr>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.frequencyCaps.employee}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.frequencyCaps.timeRange}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.frequencyCaps.maxPerWeek}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.frequencyCaps.weight}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.common.actions ?? "Actions"}
              </th>
            </tr>
          </thead>
          <tbody className={`divide-y ${border.divider}`}>
            {loading && (
              <tr>
                <td colSpan={5} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.common.loading ?? "Loading..."}
                </td>
              </tr>
            )}
            {!loading && filteredRows.length === 0 && (
              <tr>
                <td colSpan={5} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.frequencyCaps.noCaps}
                </td>
              </tr>
            )}
            {filteredRows.map((row) => {
              const empName = empById.get(row.employee_id)?.full_name ?? row.employee_id;
              const dirty = isDirty(row);
              return (
                <tr key={row.id} className={bg.rowHover}>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>{empName}</td>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>
                    {row.start_time} – {row.end_time}
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="number"
                      min={0}
                      step={1}
                      value={row.max_per_week}
                      onChange={(e) => handleMaxChange(row.id, e.target.value)}
                      className="glass-input w-20"
                      disabled={row.saving}
                    />
                  </td>
                  <td className="px-4 py-3 min-w-[16rem]">
                    <WeightSlider
                      value={row.weight}
                      onChange={(v) => handleWeightChange(row.id, v)}
                      label=""
                      hardWarning={t.frequencyCaps.hardWarning}
                    />
                    {row.error && (
                      <div className="text-xs text-red-500 mt-1">{row.error}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleSave(row.id)}
                        disabled={!dirty || row.saving}
                        className={`${action.edit} disabled:opacity-30 disabled:cursor-not-allowed`}
                      >
                        {row.saving ? t.common.saving ?? "Saving..." : t.common.save}
                      </button>
                      <button
                        onClick={() => handleDelete(row.id)}
                        disabled={row.saving}
                        className={`${action.delete} disabled:opacity-30 disabled:cursor-not-allowed`}
                      >
                        {t.common.delete}
                      </button>
                    </div>
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
