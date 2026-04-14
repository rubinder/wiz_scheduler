import { useCallback, useEffect, useMemo, useState } from "react";
import { listEmployees, updateEmployee } from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
import { action, bg, border, text } from "../../theme";
import type { Employee } from "../../types";

interface DraftRow {
  id: string;
  full_name: string;
  value: string; // input state as string so we can distinguish empty from 0
  original: number | null;
  saving: boolean;
  error: string | null;
}

function toDraft(emp: Employee): DraftRow {
  return {
    id: emp.id,
    full_name: emp.full_name,
    value: emp.max_hours_per_week == null ? "" : String(emp.max_hours_per_week),
    original: emp.max_hours_per_week ?? null,
    saving: false,
    error: null,
  };
}

export default function HourRestrictions() {
  const { t } = useLanguage();
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const emps = await listEmployees();
      setRows(emps.map(toDraft));
      setLoadError("");
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : "Failed to load employees");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const filteredRows = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return rows;
    return rows.filter((r) => r.full_name.toLowerCase().includes(f));
  }, [rows, filter]);

  const handleChange = (id: string, value: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, value, error: null } : r))
    );
  };

  const parseValue = (raw: string): number | null | "invalid" => {
    const trimmed = raw.trim();
    if (trimmed === "") return null;
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 168) {
      return "invalid";
    }
    return parsed;
  };

  const isDirty = (row: DraftRow): boolean => {
    const parsed = parseValue(row.value);
    if (parsed === "invalid") return true;
    return parsed !== row.original;
  };

  const handleSave = async (id: string) => {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    const parsed = parseValue(row.value);
    if (parsed === "invalid") {
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? { ...r, error: t.hourRestrictions.invalidValue }
            : r
        )
      );
      return;
    }
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, saving: true, error: null } : r))
    );
    try {
      const updated = await updateEmployee(id, { max_hours_per_week: parsed });
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? {
                ...r,
                original: updated.max_hours_per_week ?? null,
                value:
                  updated.max_hours_per_week == null
                    ? ""
                    : String(updated.max_hours_per_week),
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
            ? {
                ...r,
                saving: false,
                error: err instanceof Error ? err.message : "Save failed",
              }
            : r
        )
      );
    }
  };

  const handleClear = async (id: string) => {
    setRows((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, value: "", saving: true, error: null } : r
      )
    );
    try {
      await updateEmployee(id, { max_hours_per_week: null });
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? { ...r, original: null, value: "", saving: false, error: null }
            : r
        )
      );
    } catch (err: unknown) {
      setRows((prev) =>
        prev.map((r) =>
          r.id === id
            ? {
                ...r,
                saving: false,
                error: err instanceof Error ? err.message : "Save failed",
              }
            : r
        )
      );
    }
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.hourRestrictions.title}
        </h1>
        <p className={`mt-1 text-sm ${text.muted} max-w-2xl`}>
          {t.hourRestrictions.description}
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

      <div className="mb-4 max-w-sm">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t.hourRestrictions.searchPlaceholder}
          className="glass-input w-full"
        />
      </div>

      <div className="glass-card overflow-hidden">
        <table className="w-full">
          <thead className={`${bg.tableHeader} border-b ${border.default}`}>
            <tr>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.hourRestrictions.employee}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.hourRestrictions.maxHoursLabel}
              </th>
              <th className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wide ${text.muted}`}>
                {t.common.actions ?? "Actions"}
              </th>
            </tr>
          </thead>
          <tbody className={`divide-y ${border.divider}`}>
            {loading && (
              <tr>
                <td colSpan={3} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.common.loading ?? "Loading..."}
                </td>
              </tr>
            )}
            {!loading && filteredRows.length === 0 && (
              <tr>
                <td colSpan={3} className={`px-4 py-6 text-sm ${text.muted}`}>
                  {t.hourRestrictions.noEmployees}
                </td>
              </tr>
            )}
            {filteredRows.map((row) => {
              const dirty = isDirty(row);
              return (
                <tr key={row.id} className={bg.rowHover}>
                  <td className={`px-4 py-3 text-sm ${text.primary}`}>
                    {row.full_name}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={0}
                        max={168}
                        step={0.5}
                        value={row.value}
                        onChange={(e) => handleChange(row.id, e.target.value)}
                        placeholder={t.hourRestrictions.noCapPlaceholder}
                        className="glass-input w-28"
                        disabled={row.saving}
                      />
                      <span className={`text-xs ${text.muted}`}>
                        {t.hourRestrictions.hoursPerWeek}
                      </span>
                    </div>
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
                      {row.original !== null && (
                        <button
                          onClick={() => handleClear(row.id)}
                          disabled={row.saving}
                          className={`${action.delete} disabled:opacity-30 disabled:cursor-not-allowed`}
                        >
                          {t.hourRestrictions.clearCap}
                        </button>
                      )}
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
