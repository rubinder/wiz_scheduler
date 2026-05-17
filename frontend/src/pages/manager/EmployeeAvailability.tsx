import { useEffect, useMemo, useState } from "react";
import {
  createAvailability,
  deleteAvailability,
  listAllAvailability,
  listEmployees,
} from "../../api/employees";
import {
  importAvailabilitiesFrom7Shifts,
  type ImportAvailabilitiesResult,
} from "../../api/import7shifts";
import {
  importAvailabilitiesFromDeputy,
  type DeputyAvailImportResult,
} from "../../api/importDeputy";
import { listRoles } from "../../api/roles";
import DemoGuard from "../../components/shared/DemoGuard";
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import { useLanguage } from "../../i18n/LanguageContext";
import type {
  Employee,
  EmployeeAvailability,
} from "../../types";
import { text, bg, border, spinner as spinnerClass } from "../../theme";
import {
  formatDate,
  formatTime,
  isAllDay,
} from "./_employeesShared";

// ── Main component ──

export default function EmployeeAvailability() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Availability state
  const [allAvailability, setAllAvailability] = useState<
    EmployeeAvailability[]
  >([]);
  const [availLoading, setAvailLoading] = useState(false);
  const [availFilter, setAvailFilter] = useState("");
  const [addingAvail, setAddingAvail] = useState(false);
  const [availForm, setAvailForm] = useState({
    employee_id: "",
    date: "",
    start_time: "00:00",
    end_time: "23:59",
  });

  // 7shifts availability import state
  const [showImport7Shifts, setShowImport7Shifts] = useState(false);
  const [import7Token, setImport7Token] = useState("");
  const [import7Start, setImport7Start] = useState(() => {
    return new Date().toISOString().split("T")[0];
  });
  const [import7End, setImport7End] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().split("T")[0];
  });
  const [importing7Shifts, setImporting7Shifts] = useState(false);
  const [import7Result, setImport7Result] =
    useState<ImportAvailabilitiesResult | null>(null);

  // Deputy availability import state
  const [showImportDeputy, setShowImportDeputy] = useState(false);
  const [deputyToken, setDeputyToken] = useState("");
  const [deputyBaseUrl, setDeputyBaseUrl] = useState("");
  const [deputyStart, setDeputyStart] = useState(() => {
    return new Date().toISOString().split("T")[0];
  });
  const [deputyEnd, setDeputyEnd] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().split("T")[0];
  });
  const [importingDeputy, setImportingDeputy] = useState(false);
  const [deputyResult, setDeputyResult] =
    useState<DeputyAvailImportResult | null>(null);

  // Week selector: generate weeks from 4 weeks back to 8 weeks ahead
  const weekOptions = useMemo(() => {
    const today = new Date();
    // Find this week's Monday
    const dayOfWeek = today.getDay();
    const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() + mondayOffset);

    const weeks: { value: string; label: string }[] = [];
    for (let i = -4; i <= 8; i++) {
      const monday = new Date(thisMonday);
      monday.setDate(thisMonday.getDate() + i * 7);
      const sunday = new Date(monday);
      sunday.setDate(monday.getDate() + 6);
      const value = monday.toISOString().split("T")[0];
      const monLabel = monday.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      const sunLabel = sunday.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      const prefix = i === 0 ? `${t.association.weekLabel} ` : "";
      weeks.push({ value, label: `${prefix}${monLabel} – ${sunLabel}` });
    }
    return weeks;
  }, [t]);

  const [selectedWeek, setSelectedWeek] = useState(() => {
    // Default to this week's Monday
    const today = new Date();
    const dayOfWeek = today.getDay();
    const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() + mondayOffset);
    return thisMonday.toISOString().split("T")[0];
  });

  const [roleMap, setRoleMap] = useState<Map<string, string>>(new Map());

  const loadData = async () => {
    try {
      setLoading(true);
      const [employeeData, rolesData] = await Promise.all([
        listEmployees(),
        listRoles(),
      ]);
      setEmployees(employeeData);
      setRoleMap(new Map(rolesData.map((r) => [r.id, r.name])));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  };

  const loadAvailability = async (weekStart: string) => {
    setAvailLoading(true);
    try {
      const data = await listAllAvailability(weekStart);
      setAllAvailability(data);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load availability"
      );
    } finally {
      setAvailLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    loadAvailability(selectedWeek);
  }, [selectedWeek]);

  // ── Availability handlers ──

  const handleAddAvailability = async () => {
    if (!availForm.employee_id || !availForm.date) return;
    setError(null);
    setSaving(true);
    const dateObj = new Date(availForm.date);
    try {
      await createAvailability({
        employee_id: availForm.employee_id,
        year: dateObj.getFullYear(),
        month: dateObj.getMonth() + 1,
        day: dateObj.getDate(),
        start_time: `${availForm.date}T${availForm.start_time}:00`,
        end_time: `${availForm.date}T${availForm.end_time}:00`,
      });
      setAddingAvail(false);
      setAvailForm({ employee_id: "", date: "", start_time: "00:00", end_time: "23:59" });
      await loadAvailability(selectedWeek);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteAvailability = async (id: string) => {
    try {
      await deleteAvailability(id);
      setAllAvailability((prev) => prev.filter((a) => a.id !== id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  // 7shifts import date constraints
  const import7MinDate = useMemo(() => {
    const today = new Date();
    const day = today.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const monday = new Date(today);
    monday.setDate(today.getDate() + mondayOffset);
    return monday.toISOString().split("T")[0];
  }, []);

  const import7MaxDate = useMemo(() => {
    const today = new Date();
    const day = today.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const monday = new Date(today);
    monday.setDate(today.getDate() + mondayOffset + 9 * 7); // 8 weeks ahead end
    return monday.toISOString().split("T")[0];
  }, []);

  const handleImport7Shifts = async () => {
    if (!import7Token || !import7Start || !import7End) return;
    setImporting7Shifts(true);
    setError(null);
    setImport7Result(null);
    try {
      const result = await importAvailabilitiesFrom7Shifts(
        import7Token,
        import7Start,
        import7End
      );
      setImport7Result(result);
      await loadAvailability(selectedWeek);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to import availabilities from 7shifts"
      );
    } finally {
      setImporting7Shifts(false);
    }
  };

  const handleImportDeputy = async () => {
    if (!deputyToken || !deputyBaseUrl || !deputyStart || !deputyEnd) return;
    setImportingDeputy(true);
    setError(null);
    setDeputyResult(null);
    try {
      const result = await importAvailabilitiesFromDeputy(
        deputyToken,
        deputyBaseUrl,
        deputyStart,
        deputyEnd
      );
      setDeputyResult(result);
      await loadAvailability(selectedWeek);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to import availabilities from Deputy"
      );
    } finally {
      setImportingDeputy(false);
    }
  };

  // Group availability by employee
  const availByEmployee = useMemo(() => {
    const map = new Map<string, EmployeeAvailability[]>();
    for (const a of allAvailability) {
      const list = map.get(a.employee_id) || [];
      list.push(a);
      map.set(a.employee_id, list);
    }
    return map;
  }, [allAvailability]);

  // Employees with no availability records => "always available"
  const filteredEmployees = useMemo(() => {
    const q = availFilter.toLowerCase();
    return employees.filter((e) => {
      if (!q) return true;
      return e.full_name.toLowerCase().includes(q) || (e.email ?? "").toLowerCase().includes(q);
    });
  }, [employees, availFilter]);

  // ── Render ──

  if (loading) {
    return (
      <div className={`flex items-center justify-center h-64 ${text.muted}`}>
        {t.common.loading}
      </div>
    );
  }

  if (employees.length === 0) {
    return (
      <div className={`text-center py-16 ${text.muted}`}>
        <p className="text-lg">{t.employeeAvailability.noEmployees}</p>
        <p className="mt-2">
          {t.employeeAvailability.addEmployeesFirst}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.employeeAvailability.title}
        </h1>
      </div>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <label className={`text-sm font-medium ${text.muted}`}>{t.association.weekLabel}</label>
            <select
              value={selectedWeek}
              onChange={(e) => setSelectedWeek(e.target.value)}
              className="glass-input"
            >
              {weekOptions.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder={t.association.filterPlaceholder}
              value={availFilter}
              onChange={(e) => setAvailFilter(e.target.value)}
              className="glass-input w-48"
            />
          </div>
          <div className="flex items-center gap-2">
            <DemoGuard>
              <button
                onClick={() => {
                  setShowImport7Shifts(!showImport7Shifts);
                  setImport7Result(null);
                  if (!showImport7Shifts) { setShowImportDeputy(false); setDeputyResult(null); }
                }}
                className={`px-4 py-2 text-sm font-medium rounded border ${
                  showImport7Shifts
                    ? "bg-orange-500/15 border-orange-400/20 text-orange-300"
                    : "glass-btn-secondary border"
                }`}
              >
                {t.association.importFrom7shifts}
              </button>
            </DemoGuard>
            <DemoGuard>
              <button
                onClick={() => {
                  setShowImportDeputy(!showImportDeputy);
                  setDeputyResult(null);
                  if (!showImportDeputy) { setShowImport7Shifts(false); setImport7Result(null); }
                }}
                className={`px-4 py-2 text-sm font-medium rounded border ${
                  showImportDeputy
                    ? "bg-blue-500/15 border-blue-400/20 text-blue-300"
                    : "glass-btn-secondary border"
                }`}
              >
                {t.association.importFromDeputy}
              </button>
            </DemoGuard>
            <DemoGuard>
              <button
                onClick={() => setAddingAvail(true)}
                disabled={addingAvail}
                className="glass-btn-primary disabled:opacity-50"
              >
                {t.association.addAvailability}
              </button>
            </DemoGuard>
          </div>
        </div>

        {/* 7shifts import panel */}
        {showImport7Shifts && (
          <div className="bg-orange-500/[0.07] backdrop-blur-xl border border-orange-400/[0.12] rounded-xl p-4 mb-4">
            <h3 className={`text-sm font-semibold ${text.body} mb-1`}>
              {t.association.import7shiftsTitle}
            </h3>
            <p className={`text-xs ${text.muted} mb-3`}>
              {t.association.import7shiftsDesc}
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[200px]">
                <label className="glass-label-sm">
                  {t.association.accessToken7shifts}
                </label>
                <input
                  type="password"
                  value={import7Token}
                  onChange={(e) => setImport7Token(e.target.value)}
                  placeholder={t.association.accessTokenPlaceholder}
                  className="glass-input w-full"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.association.from}
                </label>
                <input
                  type="date"
                  value={import7Start}
                  min={import7MinDate}
                  max={import7MaxDate}
                  onChange={(e) => setImport7Start(e.target.value)}
                  className="glass-input"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.association.to}
                </label>
                <input
                  type="date"
                  value={import7End}
                  min={import7Start || import7MinDate}
                  max={import7MaxDate}
                  onChange={(e) => setImport7End(e.target.value)}
                  className="glass-input"
                />
              </div>
              <button
                onClick={handleImport7Shifts}
                disabled={
                  importing7Shifts || !import7Token || !import7Start || !import7End
                }
                className="glass-btn-orange disabled:opacity-50"
              >
                {importing7Shifts ? t.association.importing : t.common.import}
              </button>
              <button
                onClick={() => {
                  setShowImport7Shifts(false);
                  setImport7Result(null);
                }}
                className="glass-btn-secondary"
              >
                {t.common.cancel}
              </button>
            </div>
            {import7Result && (
              <div className="mt-3 glass-card border border-orange-400/10 p-3 text-sm">
                <p className={`font-medium ${text.secondary}`}>{t.association.importComplete}</p>
                <ul className={`mt-1 ${text.secondary} space-y-0.5`}>
                  <li>{t.association.created} {import7Result.created} {t.association.availWindows}</li>
                  <li>{t.association.cleared} {import7Result.cleared} {t.association.existingWindows}</li>
                  {import7Result.skipped > 0 && (
                    <li>{t.association.skipped} {import7Result.skipped} {t.association.unmatchedOrDeclined}</li>
                  )}
                  {import7Result.outside_range > 0 && (
                    <li className="text-gray-500">{t.association.outsideDateRange} {import7Result.outside_range}</li>
                  )}
                </ul>
                {import7Result.errors.length > 0 && (
                  <div className="mt-2">
                    <p className="text-red-400 font-medium">
                      {t.common.errors} ({import7Result.errors.length}):
                    </p>
                    <ul className="list-disc list-inside text-red-400 text-xs mt-1">
                      {import7Result.errors.map((err, i) => (
                        <li key={i}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Deputy import panel */}
        {showImportDeputy && (
          <div className="bg-blue-500/[0.07] backdrop-blur-xl border border-blue-400/[0.12] rounded-xl p-4 mb-4">
            <h3 className={`text-sm font-semibold ${text.body} mb-1`}>
              {t.association.importDeputyTitle}
            </h3>
            <p className={`text-xs ${text.muted} mb-3`}>
              {t.association.importDeputyDesc}
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[180px]">
                <label className="glass-label-sm">
                  {t.association.deputyUrl}
                </label>
                <input
                  type="text"
                  value={deputyBaseUrl}
                  onChange={(e) => setDeputyBaseUrl(e.target.value)}
                  placeholder="https://yourco.na.deputy.com"
                  className="glass-input w-full"
                />
              </div>
              <div className="min-w-[180px]">
                <label className="glass-label-sm">
                  {t.association.deputyAccessToken}
                </label>
                <input
                  type="password"
                  value={deputyToken}
                  onChange={(e) => setDeputyToken(e.target.value)}
                  placeholder={t.association.accessTokenPlaceholder}
                  className="glass-input w-full"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.association.from}
                </label>
                <input
                  type="date"
                  value={deputyStart}
                  onChange={(e) => setDeputyStart(e.target.value)}
                  className="glass-input"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.association.to}
                </label>
                <input
                  type="date"
                  value={deputyEnd}
                  min={deputyStart}
                  onChange={(e) => setDeputyEnd(e.target.value)}
                  className="glass-input"
                />
              </div>
              <button
                onClick={handleImportDeputy}
                disabled={
                  importingDeputy || !deputyToken || !deputyBaseUrl || !deputyStart || !deputyEnd
                }
                className="glass-btn-primary disabled:opacity-50"
              >
                {importingDeputy ? t.association.importing : t.common.import}
              </button>
              <button
                onClick={() => {
                  setShowImportDeputy(false);
                  setDeputyResult(null);
                }}
                className="glass-btn-secondary"
              >
                {t.common.cancel}
              </button>
            </div>
            {deputyResult && (
              <div className="mt-3 glass-card border border-blue-400/10 p-3 text-sm">
                <p className={`font-medium ${text.secondary}`}>{t.association.importComplete}</p>
                <ul className={`mt-1 ${text.secondary} space-y-0.5`}>
                  <li>{t.association.created} {deputyResult.created} {t.association.availWindows}</li>
                  <li>{t.association.cleared} {deputyResult.cleared} {t.association.existingWindows}</li>
                  {deputyResult.skipped > 0 && (
                    <li>{t.association.skipped} {deputyResult.skipped} {t.association.deputySkippedEmployees}</li>
                  )}
                </ul>
                {deputyResult.errors.length > 0 && (
                  <div className="mt-2">
                    <p className="text-red-400 font-medium">
                      {t.common.errors} ({deputyResult.errors.length}):
                    </p>
                    <ul className="list-disc list-inside text-red-400 text-xs mt-1">
                      {deputyResult.errors.map((err, i) => (
                        <li key={i}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Add availability form */}
        {addingAvail && (
          <div className={`${bg.accentPanel} rounded-xl p-4 mb-4`}>
            <h3 className={`text-sm font-semibold ${text.body} mb-3`}>
              {t.association.addAvailWindow}
            </h3>
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-56">
                <label className="glass-label-sm">
                  {t.common.employee}
                </label>
                <EmployeeSearchBox
                  employees={employees}
                  value={availForm.employee_id}
                  onChange={(id) =>
                    setAvailForm((f) => ({ ...f, employee_id: id }))
                  }
                  placeholder={t.association.searchEmployee}
                  inline
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.common.date}
                </label>
                <input
                  type="date"
                  value={availForm.date}
                  onChange={(e) =>
                    setAvailForm((f) => ({ ...f, date: e.target.value }))
                  }
                  className="glass-input"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.common.start}
                </label>
                <input
                  type="time"
                  value={availForm.start_time}
                  onChange={(e) =>
                    setAvailForm((f) => ({
                      ...f,
                      start_time: e.target.value,
                    }))
                  }
                  className="glass-input"
                />
              </div>
              <div>
                <label className="glass-label-sm">
                  {t.common.end}
                </label>
                <input
                  type="time"
                  value={availForm.end_time}
                  onChange={(e) =>
                    setAvailForm((f) => ({
                      ...f,
                      end_time: e.target.value,
                    }))
                  }
                  className="glass-input"
                />
              </div>
              <button
                onClick={handleAddAvailability}
                disabled={
                  saving || !availForm.employee_id || !availForm.date
                }
                className="glass-btn-success disabled:opacity-50"
              >
                {saving ? t.common.saving : t.common.add}
              </button>
              <button
                onClick={() => setAddingAvail(false)}
                className="glass-btn-secondary"
              >
                {t.common.cancel}
              </button>
            </div>
          </div>
        )}

        {availLoading && (
          <div className={`flex items-center gap-3 py-8 justify-center ${text.muted}`}>
            <div className={`h-5 w-5 animate-spin rounded-full border-2 ${spinnerClass}`} />
            {t.association.loadingAvailability}
          </div>
        )}

        {!availLoading && (
          <div className="space-y-3">
            {filteredEmployees.filter((emp) => (availByEmployee.get(emp.id) || []).length > 0).length === 0 && (
              <div className={`text-center py-8 ${text.muted} text-sm`}>
                {t.association.noAvailThisWeek}
              </div>
            )}
            {filteredEmployees.map((emp) => {
              const windows = availByEmployee.get(emp.id) || [];
              if (windows.length === 0) return null;

              return (
                <div
                  key={emp.id}
                  className="glass-card overflow-hidden"
                >
                  <div className={`px-4 py-3 border-b ${border.subtle} flex items-center justify-between`}>
                    <div className="flex items-center gap-3">
                      <span className={`text-sm font-semibold ${text.body}`}>
                        {emp.full_name}
                      </span>
                      {emp.email && (
                        <span className={`text-xs ${text.muted}`}>
                          {emp.email}
                        </span>
                      )}
                      {emp.roles.length > 0 && (
                        <span className={`text-xs ${text.muted}`}>
                          {emp.roles
                            .map((r) => roleMap.get(r.role_id) ?? r.role_id)
                            .join(", ")}
                        </span>
                      )}
                    </div>
                    <span className={`text-xs ${text.muted}`}>
                      {windows.length} {windows.length !== 1 ? t.association.windows : t.association.window}
                    </span>
                  </div>

                  <div className="divide-y divide-sage/10">
                      {windows
                        .sort(
                          (a, b) =>
                            formatDate(a.year, a.month, a.day).localeCompare(
                              formatDate(b.year, b.month, b.day)
                            )
                        )
                        .map((w) => (
                          <div
                            key={w.id}
                            className="px-4 py-2 flex items-center justify-between text-sm"
                          >
                            <div className="flex items-center gap-4">
                              <span className={`${text.secondary} font-medium w-28`}>
                                {formatDate(w.year, w.month, w.day)}
                              </span>
                              {isAllDay(w.start_time, w.end_time) ? (
                                <span className="text-emerald-400 text-xs font-medium">
                                  {t.association.allDay}
                                </span>
                              ) : (
                                <span className="text-gray-500">
                                  {formatTime(w.start_time)} &ndash;{" "}
                                  {formatTime(w.end_time)}
                                </span>
                              )}
                            </div>
                            <button
                              onClick={() => handleDeleteAvailability(w.id)}
                              className="text-red-400 hover:text-red-300 text-xs font-medium"
                            >
                              {t.common.remove}
                            </button>
                          </div>
                        ))}
                    </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
