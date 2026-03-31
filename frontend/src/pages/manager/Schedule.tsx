import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as schedulesApi from "../../api/schedules";
import * as shiftTemplatesApi from "../../api/shiftTemplates";
import * as locationsApi from "../../api/locations";
import { listEmployees } from "../../api/employees";
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import StatusBadge from "../../components/shared/StatusBadge";
import { useScheduleStream } from "../../hooks/useScheduleStream";
import type {
  Employee,
  Location,
  LocationResult,
  ShiftAssignment,
  ShiftTemplate,
} from "../../types";

// ── helpers ──

const dayLabelCache: Record<string, string> = {};
function getDayLabel(dateStr: string): string {
  if (dayLabelCache[dateStr]) return dayLabelCache[dateStr];
  const d = new Date(dateStr + "T00:00:00");
  const label =
    d.toLocaleDateString("en-US", { weekday: "short" }) +
    " " +
    d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  dayLabelCache[dateStr] = label;
  return label;
}

function formatTime(t: string): string {
  if (t.includes("T")) {
    const d = new Date(t);
    return d.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  }
  const [hStr, mStr] = t.split(":");
  const h = parseInt(hStr, 10);
  const m = mStr ?? "00";
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m} ${ampm}`;
}

/** Extract HH:MM from an ISO datetime or HH:MM string */
function toTimeInput(t: string): string {
  if (t.includes("T")) {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  return t.slice(0, 5);
}

const ROLE_COLORS = [
  "bg-blue-50 border-blue-200 text-blue-800",
  "bg-emerald-50 border-emerald-200 text-emerald-800",
  "bg-purple-50 border-purple-200 text-purple-800",
  "bg-amber-50 border-amber-200 text-amber-800",
  "bg-rose-50 border-rose-200 text-rose-800",
  "bg-cyan-50 border-cyan-200 text-cyan-800",
  "bg-indigo-50 border-indigo-200 text-indigo-800",
  "bg-orange-50 border-orange-200 text-orange-800",
];

// ── EditShiftModal ──

interface EditShiftModalProps {
  shift: ShiftAssignment;
  employees: Employee[];
  roles: string[];
  onSave: (updated: ShiftAssignment) => void;
  onDelete: () => void;
  onClose: () => void;
}

function EditShiftModal({
  shift,
  employees,
  roles,
  onSave,
  onDelete,
  onClose,
}: EditShiftModalProps) {
  const [employeeId, setEmployeeId] = useState(shift.employee_id);
  const [roleName, setRoleName] = useState(shift.role_name);
  const [startTime, setStartTime] = useState(toTimeInput(shift.start_time));
  const [endTime, setEndTime] = useState(toTimeInput(shift.end_time));

  const selectedEmployee = employees.find((e) => e.id === employeeId);

  const handleSave = () => {
    // Rebuild start/end using the shift date
    const datePrefix = shift.date;
    onSave({
      ...shift,
      employee_id: employeeId,
      employee_name: selectedEmployee?.full_name ?? shift.employee_name,
      role_name: roleName,
      start_time: `${datePrefix}T${startTime}:00`,
      end_time: `${datePrefix}T${endTime}:00`,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h3 className="text-lg font-semibold">Edit Shift</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Employee
            </label>
            <EmployeeSearchBox
              employees={employees}
              value={employeeId}
              onChange={setEmployeeId}
              placeholder="Search employee..."
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Role
            </label>
            <select
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm"
            >
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Start Time
              </label>
              <input
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                End Time
              </label>
              <input
                type="time"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
              />
            </div>
          </div>

          <div className="text-sm text-gray-500">
            Date: {getDayLabel(shift.date)} ({shift.date})
          </div>
        </div>

        <div className="flex justify-between items-center px-6 py-4 border-t bg-gray-50 rounded-b-lg">
          <button
            onClick={onDelete}
            className="px-3 py-2 bg-red-100 text-red-700 rounded hover:bg-red-200 text-sm font-medium"
          >
            Remove Shift
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!employeeId}
              className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ScheduleGrid ──

interface ScheduleGridProps {
  shifts: ShiftAssignment[];
  editable: boolean;
  employees: Employee[];
  onEditShift?: (shiftIndex: number) => void;
}

function ScheduleGrid({
  shifts,
  editable,
  onEditShift,
}: ScheduleGridProps) {
  const { dates, roles, grid, roleColorMap, shiftIndexMap } = useMemo(() => {
    const dateSet = new Set<string>();
    const roleSet = new Set<string>();
    shifts.forEach((s) => {
      dateSet.add(s.date);
      roleSet.add(s.role_name);
    });
    const sortedDates = Array.from(dateSet).sort();
    const sortedRoles = Array.from(roleSet).sort();

    const g: Record<string, Record<string, ShiftAssignment[]>> = {};
    // Map each shift in the grid back to its index in the flat array
    const indexMap: Record<string, Record<string, number[]>> = {};
    for (const role of sortedRoles) {
      g[role] = {};
      indexMap[role] = {};
      for (const date of sortedDates) {
        g[role][date] = [];
        indexMap[role][date] = [];
      }
    }
    shifts.forEach((s, i) => {
      g[s.role_name][s.date].push(s);
      indexMap[s.role_name][s.date].push(i);
    });

    const colorMap: Record<string, string> = {};
    sortedRoles.forEach((r, i) => {
      colorMap[r] = ROLE_COLORS[i % ROLE_COLORS.length];
    });

    return {
      dates: sortedDates,
      roles: sortedRoles,
      grid: g,
      roleColorMap: colorMap,
      shiftIndexMap: indexMap,
    };
  }, [shifts]);

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full">
        <thead>
          <tr className="bg-gray-50">
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase sticky left-0 bg-gray-50 z-10 min-w-[140px]">
              Role
            </th>
            {dates.map((d) => (
              <th
                key={d}
                className="px-3 py-3 text-center text-xs font-semibold text-gray-500 uppercase min-w-[160px]"
              >
                {getDayLabel(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role} className="border-t border-gray-100">
              <td className="px-4 py-3 text-sm font-medium text-gray-700 align-top sticky left-0 bg-white z-10">
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-semibold border ${roleColorMap[role]}`}
                >
                  {role}
                </span>
              </td>
              {dates.map((date) => {
                const cellShifts = grid[role][date];
                const cellIndices = shiftIndexMap[role][date];
                return (
                  <td key={date} className="px-3 py-3 align-top">
                    {cellShifts.length === 0 ? (
                      <span className="text-gray-300 text-xs">&mdash;</span>
                    ) : (
                      <div className="space-y-1.5">
                        {cellShifts.map((s, i) => (
                          <div
                            key={i}
                            onClick={
                              editable && onEditShift
                                ? () => onEditShift(cellIndices[i])
                                : undefined
                            }
                            className={`rounded-md border px-2.5 py-1.5 ${roleColorMap[role]} ${
                              editable
                                ? "cursor-pointer hover:ring-2 hover:ring-indigo-400 transition-shadow"
                                : ""
                            }`}
                          >
                            <div className="text-sm font-medium">
                              {s.employee_name}
                            </div>
                            <div className="text-xs opacity-75">
                              {formatTime(s.start_time)} &ndash;{" "}
                              {formatTime(s.end_time)}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main ──

function getNextMonday(): string {
  const now = new Date();
  const day = now.getDay();
  const diff = day === 0 ? 1 : 8 - day;
  const monday = new Date(now);
  monday.setDate(now.getDate() + diff);
  return monday.toISOString().split("T")[0];
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + days);
  return d.toISOString().split("T")[0];
}

function daysBetween(startStr: string, endStr: string): number {
  const s = new Date(startStr + "T00:00:00");
  const e = new Date(endStr + "T00:00:00");
  return Math.round((e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24)) + 1;
}

export default function Schedule() {
  const [startDate, setStartDate] = useState(getNextMonday());
  const [endDate, setEndDate] = useState(addDays(getNextMonday(), 6));
  const weekStart = startDate; // alias for compatibility
  const { results, isStreaming, error, generate, reset } =
    useScheduleStream();
  const [actionError, setActionError] = useState("");
  const [approvedLocations, setApprovedLocations] = useState<Set<string>>(
    new Set()
  );
  const [rejectedLocations, setRejectedLocations] = useState<Set<string>>(
    new Set()
  );

  // Editable shifts: keyed by location_id
  const [editedShifts, setEditedShifts] = useState<
    Record<string, ShiftAssignment[]>
  >({});
  const [editingShift, setEditingShift] = useState<{
    locationId: string;
    shiftIndex: number;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  // Employees for the edit modal
  const [employees, setEmployees] = useState<Employee[]>([]);
  const employeesLoaded = useRef(false);

  // Template selection state
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [templates, setTemplates] = useState<ShiftTemplate[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [selectedTemplateIds, setSelectedTemplateIds] = useState<Set<string>>(
    new Set()
  );
  const [loadingTemplates, setLoadingTemplates] = useState(false);

  // Generation mode: "ai" (LLM) or "local" (algorithmic)
  const [generateMode, setGenerateMode] = useState<"ai" | "local">("ai");
  const [localStrategy, setLocalStrategy] = useState<"random" | "rotation">("rotation");

  // Load employees once when results arrive
  useEffect(() => {
    if (results.length > 0 && !employeesLoaded.current) {
      employeesLoaded.current = true;
      listEmployees()
        .then(setEmployees)
        .catch(() => {});
    }
  }, [results.length]);

  // Initialize editedShifts when new results stream in
  useEffect(() => {
    if (results.length > 0) {
      setEditedShifts((prev) => {
        const next = { ...prev };
        for (const r of results) {
          if (!next[r.location_id]) {
            next[r.location_id] = [...r.shifts];
          }
        }
        return next;
      });
    }
  }, [results]);

  const locationName = (locId: string) =>
    locations.find((l) => l.id === locId)?.name ?? locId.slice(0, 8);

  const fetchTemplatesAndLocations = useCallback(async () => {
    setLoadingTemplates(true);
    try {
      const [tmpls, locs] = await Promise.all([
        shiftTemplatesApi.listShiftTemplates(),
        locationsApi.listLocations(),
      ]);
      setTemplates(tmpls);
      setLocations(locs);
      setSelectedTemplateIds(new Set(tmpls.map((t) => t.id)));
    } catch {
      setActionError("Failed to load templates");
    } finally {
      setLoadingTemplates(false);
    }
  }, []);

  useEffect(() => {
    if (showTemplatePicker && templates.length === 0) {
      fetchTemplatesAndLocations();
    }
  }, [showTemplatePicker, templates.length, fetchTemplatesAndLocations]);

  const handleGenerateClick = (mode: "ai" | "local") => {
    setGenerateMode(mode);
    setShowTemplatePicker(true);
    setActionError("");
  };

  const handleConfirmGenerate = () => {
    const ids = Array.from(selectedTemplateIds);
    if (ids.length === 0) {
      setActionError("Please select at least one template");
      return;
    }
    setShowTemplatePicker(false);
    setApprovedLocations(new Set());
    setRejectedLocations(new Set());
    setEditedShifts({});
    setActionError("");
    employeesLoaded.current = false;
    const numDays = daysBetween(startDate, endDate);
    generate(weekStart, undefined, ids, {
      ...(generateMode === "local" ? { useLocal: true, strategy: localStrategy } : {}),
      numDays,
    });
  };

  const toggleTemplate = (id: string) => {
    setSelectedTemplateIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllForLocation = (locId: string) => {
    const locTemplates = templates.filter((t) => t.location_id === locId);
    const allSelected = locTemplates.every((t) =>
      selectedTemplateIds.has(t.id)
    );
    setSelectedTemplateIds((prev) => {
      const next = new Set(prev);
      for (const t of locTemplates) {
        if (allSelected) next.delete(t.id);
        else next.add(t.id);
      }
      return next;
    });
  };

  // Save edited shifts to backend before approve
  const saveShifts = async (locationResult: LocationResult) => {
    const scheduleId = locationResult.schedule_id;
    if (!scheduleId) return;
    const shifts = editedShifts[locationResult.location_id];
    if (!shifts) return;
    setSaving(true);
    try {
      await schedulesApi.updateShifts(scheduleId, shifts);
    } finally {
      setSaving(false);
    }
  };

  const handleApprove = useCallback(
    async (locationResult: LocationResult) => {
      if (!locationResult.schedule_id) {
        setActionError("No schedule ID available for this location");
        return;
      }
      try {
        // Persist any edits first
        await saveShifts(locationResult);
        await schedulesApi.approveSchedule(locationResult.schedule_id);
        setApprovedLocations((prev) => {
          const next = new Set(prev);
          next.add(locationResult.location_id);
          return next;
        });
      } catch (err: unknown) {
        setActionError(
          err instanceof Error ? err.message : "Approve failed"
        );
      }
    },
    [editedShifts]
  );

  const handleReject = useCallback(
    async (locationResult: LocationResult) => {
      if (!locationResult.schedule_id) {
        setActionError("No schedule ID available for this location");
        return;
      }
      try {
        await schedulesApi.rejectSchedule(locationResult.schedule_id);
        setRejectedLocations((prev) => {
          const next = new Set(prev);
          next.add(locationResult.location_id);
          return next;
        });
      } catch (err: unknown) {
        setActionError(
          err instanceof Error ? err.message : "Reject failed"
        );
      }
    },
    []
  );

  // Edit handlers
  const handleEditShift = (locationId: string, shiftIndex: number) => {
    setEditingShift({ locationId, shiftIndex });
  };

  const handleSaveShift = (updated: ShiftAssignment) => {
    if (!editingShift) return;
    setEditedShifts((prev) => {
      const shifts = [...(prev[editingShift.locationId] ?? [])];
      shifts[editingShift.shiftIndex] = updated;
      return { ...prev, [editingShift.locationId]: shifts };
    });
    setEditingShift(null);
  };

  const handleDeleteShift = () => {
    if (!editingShift) return;
    setEditedShifts((prev) => {
      const shifts = [...(prev[editingShift.locationId] ?? [])];
      shifts.splice(editingShift.shiftIndex, 1);
      return { ...prev, [editingShift.locationId]: shifts };
    });
    setEditingShift(null);
  };

  // Collect all role names across all results for the edit modal
  const allRoles = useMemo(() => {
    const set = new Set<string>();
    for (const r of results) {
      const shifts = editedShifts[r.location_id] ?? r.shifts;
      shifts.forEach((s) => set.add(s.role_name));
    }
    return Array.from(set).sort();
  }, [results, editedShifts]);

  // Print state: which location is being printed
  const [printLocationId, setPrintLocationId] = useState<string | null>(null);

  const handlePrint = (locationId: string) => {
    setPrintLocationId(locationId);
    // Wait for React to render the print class, then print
    requestAnimationFrame(() => {
      window.print();
      setPrintLocationId(null);
    });
  };

  const allComplete =
    !isStreaming &&
    results.length > 0 &&
    results.every(
      (r) =>
        approvedLocations.has(r.location_id) ||
        rejectedLocations.has(r.location_id)
    );

  const templatesByLocation = templates.reduce<
    Record<string, ShiftTemplate[]>
  >((acc, t) => {
    if (!acc[t.location_id]) acc[t.location_id] = [];
    acc[t.location_id].push(t);
    return acc;
  }, {});

  // Find the shift being edited for the modal
  const editModalShift =
    editingShift &&
    editedShifts[editingShift.locationId]?.[editingShift.shiftIndex];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Schedule Generation</h1>

      <div className="flex items-center gap-4 mb-6 flex-wrap">
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium text-gray-700">Start:</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => {
              const v = e.target.value;
              setStartDate(v);
              setEndDate(addDays(v, 6));
            }}
            className="border border-gray-300 rounded px-3 py-2 text-sm"
          />
          <label className="text-sm font-medium text-gray-700">End:</label>
          <input
            type="date"
            value={endDate}
            min={startDate}
            max={addDays(startDate, 13)}
            onChange={(e) => setEndDate(e.target.value)}
            className="border border-gray-300 rounded px-3 py-2 text-sm"
          />
          <span className="text-xs text-gray-500">
            ({daysBetween(startDate, endDate)} day{daysBetween(startDate, endDate) !== 1 ? "s" : ""})
          </span>
        </div>
        <button
          onClick={() => handleGenerateClick("ai")}
          disabled={isStreaming}
          className="px-5 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-semibold text-sm"
        >
          {isStreaming && generateMode === "ai" ? "Generating..." : "AI Generate"}
        </button>
        <button
          onClick={() => handleGenerateClick("local")}
          disabled={isStreaming}
          className="px-5 py-3 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 font-semibold text-sm"
        >
          {isStreaming && generateMode === "local" ? "Generating..." : "Local Generate"}
        </button>
        {results.length > 0 && !isStreaming && (
          <button
            onClick={() => {
              reset();
              setEditedShifts({});
            }}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
          >
            Reset
          </button>
        )}
      </div>

      {/* Template Picker Modal */}
      {showTemplatePicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b">
              <h2 className="text-lg font-semibold">
                Select Shift Templates
              </h2>
              <button
                onClick={() => setShowTemplatePicker(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <p className="text-sm text-gray-600 mb-4">
                Choose which shift templates to generate schedules for.
                Templates are grouped by location.
              </p>

              {loadingTemplates && (
                <div className="flex items-center gap-3 py-4">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
                  <span className="text-sm text-gray-600">
                    Loading templates...
                  </span>
                </div>
              )}

              {!loadingTemplates && templates.length === 0 && (
                <p className="text-sm text-gray-500">
                  No shift templates found. Create templates first under Shift
                  Templates.
                </p>
              )}

              {!loadingTemplates &&
                Object.entries(templatesByLocation).map(
                  ([locId, locTemplates]) => {
                    const allSelected = locTemplates.every((t) =>
                      selectedTemplateIds.has(t.id)
                    );
                    const someSelected =
                      !allSelected &&
                      locTemplates.some((t) =>
                        selectedTemplateIds.has(t.id)
                      );

                    return (
                      <div
                        key={locId}
                        className="mb-4 border border-gray-200 rounded-lg"
                      >
                        <div className="px-4 py-2 bg-gray-50 rounded-t-lg flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = someSelected;
                            }}
                            onChange={() => toggleAllForLocation(locId)}
                            className="rounded border-gray-300"
                          />
                          <span className="text-sm font-semibold text-gray-700">
                            {locationName(locId)}
                          </span>
                          <span className="text-xs text-gray-400">
                            ({locTemplates.length} template
                            {locTemplates.length !== 1 ? "s" : ""})
                          </span>
                        </div>
                        <div className="px-4 py-2 space-y-1">
                          {locTemplates.map((tmpl) => (
                            <label
                              key={tmpl.id}
                              className="flex items-center gap-2 text-sm cursor-pointer py-1"
                            >
                              <input
                                type="checkbox"
                                checked={selectedTemplateIds.has(tmpl.id)}
                                onChange={() => toggleTemplate(tmpl.id)}
                                className="rounded border-gray-300"
                              />
                              {tmpl.name}
                            </label>
                          ))}
                        </div>
                      </div>
                    );
                  }
                )}
            </div>

            {generateMode === "local" && (
              <div className="px-6 py-3 border-t bg-emerald-50">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Strategy
                </label>
                <div className="flex gap-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="rotation"
                      checked={localStrategy === "rotation"}
                      onChange={() => setLocalStrategy("rotation")}
                      className="text-emerald-600"
                    />
                    <div>
                      <span className="text-sm font-medium">Rotation</span>
                      <p className="text-xs text-gray-500">Distributes shifts evenly across employees</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="random"
                      checked={localStrategy === "random"}
                      onChange={() => setLocalStrategy("random")}
                      className="text-emerald-600"
                    />
                    <div>
                      <span className="text-sm font-medium">Random</span>
                      <p className="text-xs text-gray-500">Randomly picks from eligible employees</p>
                    </div>
                  </label>
                </div>
              </div>
            )}

            <div className="flex justify-between items-center px-6 py-4 border-t bg-gray-50 rounded-b-lg">
              <span className="text-xs text-gray-500">
                {selectedTemplateIds.size} of {templates.length} selected
              </span>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowTemplatePicker(false)}
                  className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmGenerate}
                  disabled={selectedTemplateIds.size === 0}
                  className={`px-4 py-2 text-white rounded disabled:opacity-50 text-sm font-medium ${
                    generateMode === "local"
                      ? "bg-emerald-600 hover:bg-emerald-700"
                      : "bg-indigo-600 hover:bg-indigo-700"
                  }`}
                >
                  {generateMode === "local" ? "Generate Locally" : "Generate with AI"} for {selectedTemplateIds.size} Template
                  {selectedTemplateIds.size !== 1 ? "s" : ""}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Shift Modal */}
      {editModalShift && editingShift && (
        <EditShiftModal
          shift={editModalShift}
          employees={employees}
          roles={allRoles}
          onSave={handleSaveShift}
          onDelete={handleDeleteShift}
          onClose={() => setEditingShift(null)}
        />
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      {actionError && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {actionError}
        </div>
      )}

      {isStreaming && results.length === 0 && (
        <div className="text-gray-500 text-sm">
          Waiting for schedule results...
        </div>
      )}

      {allComplete && (
        <div className="mb-6 p-4 bg-green-100 text-green-800 rounded-lg text-center font-semibold">
          Schedule Complete - All locations have been reviewed.
        </div>
      )}

      <div className="space-y-6">
        {results.map((locationResult) => {
          const isApproved = approvedLocations.has(
            locationResult.location_id
          );
          const isRejected = rejectedLocations.has(
            locationResult.location_id
          );
          const decided = isApproved || isRejected;
          const currentShifts =
            editedShifts[locationResult.location_id] ?? locationResult.shifts;

          return (
            <div
              key={locationResult.location_id}
              className={`bg-white rounded-lg shadow ${
                printLocationId === locationResult.location_id ? "print-target" : ""
              }`}
              data-print={printLocationId === locationResult.location_id ? "true" : undefined}
            >
              <div className="p-4 border-b border-gray-200 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold">
                    {locationResult.location_name}
                  </h3>
                  <StatusBadge status={locationResult.status} />
                  {isApproved && <StatusBadge status="approved" />}
                  {isRejected && <StatusBadge status="rejected" />}
                </div>
                <div className="flex items-center gap-2">
                  {isApproved && (
                    <button
                      onClick={() => handlePrint(locationResult.location_id)}
                      className="px-3 py-1 bg-gray-100 text-gray-700 rounded hover:bg-gray-200 text-sm font-medium print:hidden"
                    >
                      Print
                    </button>
                  )}
                  {!decided && (
                    <span className="text-xs text-gray-400 mr-2">
                      Click a shift to edit
                    </span>
                  )}
                  {!decided && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleApprove(locationResult)}
                        disabled={saving}
                        className="px-3 py-1 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium disabled:opacity-50"
                      >
                        {saving ? "Saving..." : "Approve"}
                      </button>
                      <button
                        onClick={() => handleReject(locationResult)}
                        className="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 text-sm font-medium"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {locationResult.errors.length > 0 && (
                <div className="p-4 bg-red-50">
                  <p className="text-sm font-medium text-red-700 mb-1">
                    Errors:
                  </p>
                  <ul className="list-disc list-inside text-sm text-red-600">
                    {locationResult.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}

              {currentShifts.length > 0 && (
                <ScheduleGrid
                  shifts={currentShifts}
                  editable={!decided}
                  employees={employees}
                  onEditShift={(idx) =>
                    handleEditShift(locationResult.location_id, idx)
                  }
                />
              )}

              {currentShifts.length === 0 &&
                locationResult.errors.length === 0 && (
                  <div className="p-4 text-gray-500 text-sm">
                    No shifts generated for this location.
                  </div>
                )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
