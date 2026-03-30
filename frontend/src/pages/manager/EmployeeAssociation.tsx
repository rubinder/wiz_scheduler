import { useEffect, useMemo, useState } from "react";
import {
  createAffinity,
  deleteAffinity,
  listAffinities,
  updateAffinity,
} from "../../api/affinities";
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
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import type {
  Employee,
  EmployeeAffinity,
  EmployeeAvailability,
} from "../../types";

// ── Tab types ──

type Tab = "affinities" | "availability";

// ── Affinity tab helpers ──

interface EditingRow {
  id: string | null;
  employee_id: string;
  target_employee_id: string;
  level: string;
  entry_date: string;
  expiration_date: string;
}

const LEVEL_OPTIONS = [
  { value: "1", label: "1.0 — Must schedule together" },
  { value: "0.5", label: "0.5 — Prefer together" },
  { value: "0", label: "0.0 — Neutral" },
  { value: "-0.5", label: "-0.5 — Prefer apart" },
  { value: "-1", label: "-1.0 — Must not schedule together" },
];

const getLevelLabel = (level: number): string => {
  if (level >= 1) return "Must together";
  if (level > 0) return "Prefer together";
  if (level === 0) return "Neutral";
  if (level > -1) return "Prefer apart";
  return "Must not together";
};

const getLevelColor = (level: number): string => {
  if (level >= 1) return "bg-green-100 text-green-800";
  if (level > 0) return "bg-green-50 text-green-700";
  if (level === 0) return "bg-gray-100 text-gray-700";
  if (level > -1) return "bg-orange-100 text-orange-700";
  return "bg-red-100 text-red-800";
};

// ── Availability tab helpers ──

function formatDate(y: number, m: number, d: number) {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function formatTime(iso: string) {
  // Parse hours/minutes directly from the ISO string to avoid browser timezone conversion.
  // Availability times are stored as-is from the source (location-local intent).
  const match = iso.match(/T(\d{2}):(\d{2})/);
  if (!match) return iso;
  let hours = parseInt(match[1], 10);
  const minutes = match[2];
  const ampm = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  return `${hours}:${minutes} ${ampm}`;
}

function isAllDay(start: string, end: string): boolean {
  const sMatch = start.match(/T(\d{2}):(\d{2})/);
  const eMatch = end.match(/T(\d{2}):(\d{2})/);
  if (!sMatch || !eMatch) return false;
  return sMatch[1] === "00" && sMatch[2] === "00" && eMatch[1] === "23" && eMatch[2] === "59";
}

// ── Main component ──

export default function EmployeeAssociation() {
  const [tab, setTab] = useState<Tab>("availability");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Affinities state
  const [affinities, setAffinities] = useState<EmployeeAffinity[]>([]);
  const [editing, setEditing] = useState<EditingRow | null>(null);
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
      const prefix = i === 0 ? "This Week: " : "";
      weeks.push({ value, label: `${prefix}${monLabel} – ${sunLabel}` });
    }
    return weeks;
  }, []);

  const [selectedWeek, setSelectedWeek] = useState(() => {
    // Default to this week's Monday
    const today = new Date();
    const dayOfWeek = today.getDay();
    const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() + mondayOffset);
    return thisMonday.toISOString().split("T")[0];
  });

  const employeeMap = useMemo(() => {
    const map = new Map<string, string>();
    employees.forEach((e) => map.set(e.id, e.full_name));
    return map;
  }, [employees]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [affinityData, employeeData] = await Promise.all([
        listAffinities(),
        listEmployees(),
      ]);
      setAffinities(affinityData);
      setEmployees(employeeData);
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
    if (tab === "availability") {
      loadAvailability(selectedWeek);
    }
  }, [tab, selectedWeek]);

  // ── Affinity handlers ──

  const today = new Date().toISOString().split("T")[0];

  const startAdding = () => {
    setEditing({
      id: null,
      employee_id: "",
      target_employee_id: "",
      level: "0.5",
      entry_date: today,
      expiration_date: "",
    });
  };

  const startEditing = (a: EmployeeAffinity) => {
    setEditing({
      id: a.id,
      employee_id: a.employee_id,
      target_employee_id: a.target_employee_id,
      level: String(a.level),
      entry_date: a.entry_date,
      expiration_date: a.expiration_date ?? "",
    });
  };

  const cancelEditing = () => setEditing(null);

  const handleSave = async () => {
    if (!editing) return;
    if (editing.employee_id === editing.target_employee_id) {
      setError("Employee and target must be different");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        employee_id: editing.employee_id,
        target_employee_id: editing.target_employee_id,
        level: parseFloat(editing.level),
        entry_date: editing.entry_date,
        expiration_date: editing.expiration_date || null,
      };
      if (editing.id) {
        await updateAffinity(editing.id, payload);
      } else {
        await createAffinity(payload);
      }
      setEditing(null);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this association?")) return;
    try {
      await deleteAffinity(id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete");
    }
  };

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
      <div className="flex items-center justify-center h-64 text-gray-500">
        Loading...
      </div>
    );
  }

  if (employees.length === 0) {
    return (
      <div className="text-center py-16 text-gray-500">
        <p className="text-lg">No employees found.</p>
        <p className="mt-2">
          Add employees first before creating associations.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">
          Employee Availability &amp; Association
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Manage employee availability and scheduling affinities.
          Affinity values: <strong>1</strong> = must work together,{" "}
          <strong>0.5</strong> = nice to have together,{" "}
          <strong>-1</strong> = cannot work together,{" "}
          <strong>-0.5 / -0.7</strong> = prefer not to work together (use
          instead of -1 when you have no option but to occasionally schedule
          them together).
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-gray-200 mb-6">
        <button
          onClick={() => setTab("availability")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            tab === "availability"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
          }`}
        >
          Availability
        </button>
        <button
          onClick={() => setTab("affinities")}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            tab === "affinities"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
          }`}
        >
          Affinities
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {/* ── Affinities Tab ── */}
      {tab === "affinities" && (
        <div>
          <div className="flex justify-end mb-4">
            <button
              onClick={startAdding}
              disabled={editing !== null}
              className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded hover:bg-indigo-700 disabled:opacity-50"
            >
              + Add Association
            </button>
          </div>

          <div className="bg-white shadow rounded-lg overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Employee
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Target Employee
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Affinity Level
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Entry Date
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Expiration Date
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {editing && (
                  <tr className="bg-indigo-50 align-top">
                    <td className="px-4 py-4">
                      <EmployeeSearchBox
                        employees={employees}
                        value={editing.employee_id}
                        onChange={(id) =>
                          setEditing({ ...editing, employee_id: id })
                        }
                        excludeIds={
                          editing.target_employee_id
                            ? [editing.target_employee_id]
                            : []
                        }
                        placeholder="Type to search..."
                        inline
                      />
                    </td>
                    <td className="px-4 py-4">
                      <EmployeeSearchBox
                        employees={employees}
                        value={editing.target_employee_id}
                        onChange={(id) =>
                          setEditing({ ...editing, target_employee_id: id })
                        }
                        excludeIds={
                          editing.employee_id ? [editing.employee_id] : []
                        }
                        placeholder="Type to search..."
                        inline
                      />
                    </td>
                    <td className="px-4 py-4">
                      <select
                        value={editing.level}
                        onChange={(e) =>
                          setEditing({ ...editing, level: e.target.value })
                        }
                        className="w-full border rounded px-2 py-1 text-sm"
                      >
                        {LEVEL_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-4">
                      <input
                        type="date"
                        value={editing.entry_date}
                        onChange={(e) =>
                          setEditing({ ...editing, entry_date: e.target.value })
                        }
                        className="w-full border rounded px-2 py-1 text-sm"
                      />
                    </td>
                    <td className="px-4 py-4">
                      <input
                        type="date"
                        value={editing.expiration_date}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            expiration_date: e.target.value,
                          })
                        }
                        className="w-full border rounded px-2 py-1 text-sm"
                        placeholder="Optional"
                      />
                    </td>
                    <td className="px-4 py-4 text-right space-x-2">
                      <button
                        onClick={handleSave}
                        disabled={
                          saving ||
                          !editing.employee_id ||
                          !editing.target_employee_id ||
                          !editing.entry_date
                        }
                        className="px-3 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700 disabled:opacity-50"
                      >
                        {saving ? "Saving..." : "Save"}
                      </button>
                      <button
                        onClick={cancelEditing}
                        className="px-3 py-1 bg-gray-300 text-gray-700 text-xs rounded hover:bg-gray-400"
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                )}

                {affinities.length === 0 && !editing && (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-8 text-center text-gray-400 text-sm"
                    >
                      No associations yet. Click &quot;+ Add Association&quot;
                      to create one.
                    </td>
                  </tr>
                )}

                {affinities.map((a) =>
                  editing?.id === a.id ? null : (
                    <tr key={a.id} className="hover:bg-gray-50">
                      <td className="px-4 py-5 text-sm text-gray-900">
                        {employeeMap.get(a.employee_id) ?? a.employee_id}
                      </td>
                      <td className="px-4 py-5 text-sm text-gray-900">
                        {employeeMap.get(a.target_employee_id) ??
                          a.target_employee_id}
                      </td>
                      <td className="px-4 py-5 text-sm">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${getLevelColor(a.level)}`}
                        >
                          {a.level} — {getLevelLabel(a.level)}
                        </span>
                      </td>
                      <td className="px-4 py-5 text-sm text-gray-700">
                        {a.entry_date}
                      </td>
                      <td className="px-4 py-5 text-sm text-gray-700">
                        {a.expiration_date ?? (
                          <span className="text-gray-400">None</span>
                        )}
                      </td>
                      <td className="px-4 py-5 text-right space-x-2">
                        <button
                          onClick={() => startEditing(a)}
                          disabled={editing !== null}
                          className="px-3 py-1 bg-indigo-100 text-indigo-700 text-xs rounded hover:bg-indigo-200 disabled:opacity-50"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDelete(a.id)}
                          disabled={editing !== null}
                          className="px-3 py-1 bg-red-100 text-red-700 text-xs rounded hover:bg-red-200 disabled:opacity-50"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  )
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Availability Tab ── */}
      {tab === "availability" && (
        <div>
          <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <label className="text-sm font-medium text-gray-700">Week:</label>
              <select
                value={selectedWeek}
                onChange={(e) => setSelectedWeek(e.target.value)}
                className="border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {weekOptions.map((w) => (
                  <option key={w.value} value={w.value}>
                    {w.label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Filter by name..."
                value={availFilter}
                onChange={(e) => setAvailFilter(e.target.value)}
                className="border border-gray-300 rounded px-3 py-2 text-sm w-48"
              />
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setShowImport7Shifts(!showImport7Shifts);
                  setImport7Result(null);
                }}
                className={`px-4 py-2 text-sm font-medium rounded border ${
                  showImport7Shifts
                    ? "bg-orange-100 border-orange-300 text-orange-700"
                    : "bg-white border-gray-300 text-gray-700 hover:bg-gray-50"
                }`}
              >
                Import from 7shifts
              </button>
              <button
                onClick={() => setAddingAvail(true)}
                disabled={addingAvail}
                className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded hover:bg-indigo-700 disabled:opacity-50"
              >
                + Add Availability
              </button>
            </div>
          </div>

          {/* 7shifts import panel */}
          {showImport7Shifts && (
            <div className="bg-orange-50 rounded-lg p-4 mb-4 border border-orange-200">
              <h3 className="text-sm font-semibold text-gray-700 mb-1">
                Import Availabilities from 7shifts
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                Fetches employee availability from the 7shifts API for the
                specified date range. Existing availability in that range will
                be replaced. You can import from the current week up to 8 weeks
                ahead.
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 min-w-[200px]">
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    7shifts Access Token
                  </label>
                  <input
                    type="password"
                    value={import7Token}
                    onChange={(e) => setImport7Token(e.target.value)}
                    placeholder="Enter your 7shifts access token"
                    className="w-full border rounded px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    From
                  </label>
                  <input
                    type="date"
                    value={import7Start}
                    min={import7MinDate}
                    max={import7MaxDate}
                    onChange={(e) => setImport7Start(e.target.value)}
                    className="border rounded px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    To
                  </label>
                  <input
                    type="date"
                    value={import7End}
                    min={import7Start || import7MinDate}
                    max={import7MaxDate}
                    onChange={(e) => setImport7End(e.target.value)}
                    className="border rounded px-3 py-2 text-sm"
                  />
                </div>
                <button
                  onClick={handleImport7Shifts}
                  disabled={
                    importing7Shifts || !import7Token || !import7Start || !import7End
                  }
                  className="px-4 py-2 bg-orange-600 text-white text-sm font-medium rounded hover:bg-orange-700 disabled:opacity-50"
                >
                  {importing7Shifts ? "Importing..." : "Import"}
                </button>
                <button
                  onClick={() => {
                    setShowImport7Shifts(false);
                    setImport7Result(null);
                  }}
                  className="px-4 py-2 bg-gray-200 text-gray-700 text-sm rounded hover:bg-gray-300"
                >
                  Cancel
                </button>
              </div>
              {import7Result && (
                <div className="mt-3 p-3 bg-white rounded border border-orange-100 text-sm">
                  <p className="font-medium text-gray-700">Import Complete</p>
                  <ul className="mt-1 text-gray-600 space-y-0.5">
                    <li>Created: {import7Result.created} availability windows</li>
                    <li>Cleared: {import7Result.cleared} existing windows in range</li>
                    {import7Result.skipped > 0 && (
                      <li>Skipped: {import7Result.skipped} (unmatched or declined)</li>
                    )}
                    {import7Result.outside_range > 0 && (
                      <li className="text-gray-400">Outside date range: {import7Result.outside_range}</li>
                    )}
                  </ul>
                  {import7Result.errors.length > 0 && (
                    <div className="mt-2">
                      <p className="text-red-600 font-medium">
                        Errors ({import7Result.errors.length}):
                      </p>
                      <ul className="list-disc list-inside text-red-600 text-xs mt-1">
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

          {/* Add availability form */}
          {addingAvail && (
            <div className="bg-indigo-50 rounded-lg p-4 mb-4 border border-indigo-200">
              <h3 className="text-sm font-semibold text-gray-700 mb-3">
                Add Availability Window
              </h3>
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-56">
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Employee
                  </label>
                  <EmployeeSearchBox
                    employees={employees}
                    value={availForm.employee_id}
                    onChange={(id) =>
                      setAvailForm((f) => ({ ...f, employee_id: id }))
                    }
                    placeholder="Search employee..."
                    inline
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Date
                  </label>
                  <input
                    type="date"
                    value={availForm.date}
                    onChange={(e) =>
                      setAvailForm((f) => ({ ...f, date: e.target.value }))
                    }
                    className="border rounded px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Start
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
                    className="border rounded px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    End
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
                    className="border rounded px-3 py-2 text-sm"
                  />
                </div>
                <button
                  onClick={handleAddAvailability}
                  disabled={
                    saving || !availForm.employee_id || !availForm.date
                  }
                  className="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Add"}
                </button>
                <button
                  onClick={() => setAddingAvail(false)}
                  className="px-4 py-2 bg-gray-200 text-gray-700 text-sm rounded hover:bg-gray-300"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {availLoading && (
            <div className="flex items-center gap-3 py-8 justify-center text-gray-500">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
              Loading availability...
            </div>
          )}

          {!availLoading && (
            <div className="space-y-3">
              {filteredEmployees.filter((emp) => (availByEmployee.get(emp.id) || []).length > 0).length === 0 && (
                <div className="text-center py-8 text-gray-400 text-sm">
                  No employees have availability set for this week.
                </div>
              )}
              {filteredEmployees.map((emp) => {
                const windows = availByEmployee.get(emp.id) || [];
                if (windows.length === 0) return null;

                return (
                  <div
                    key={emp.id}
                    className="bg-white shadow rounded-lg overflow-hidden"
                  >
                    <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-semibold text-gray-800">
                          {emp.full_name}
                        </span>
                        {emp.email && (
                          <span className="text-xs text-gray-400">
                            {emp.email}
                          </span>
                        )}
                      </div>
                      <span className="text-xs text-gray-500">
                        {windows.length} window{windows.length !== 1 ? "s" : ""}
                      </span>
                    </div>

                    <div className="divide-y divide-gray-50">
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
                                <span className="text-gray-700 font-medium w-28">
                                  {formatDate(w.year, w.month, w.day)}
                                </span>
                                {isAllDay(w.start_time, w.end_time) ? (
                                  <span className="text-green-600 text-xs font-medium">
                                    All Day
                                  </span>
                                ) : (
                                  <span className="text-gray-600">
                                    {formatTime(w.start_time)} &ndash;{" "}
                                    {formatTime(w.end_time)}
                                  </span>
                                )}
                              </div>
                              <button
                                onClick={() => handleDeleteAvailability(w.id)}
                                className="text-red-500 hover:text-red-700 text-xs font-medium"
                              >
                                Remove
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
      )}
    </div>
  );
}
