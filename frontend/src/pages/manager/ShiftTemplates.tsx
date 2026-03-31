import { useCallback, useEffect, useMemo, useState } from "react";
import * as condensedRolesApi from "../../api/condensedRoles";
import * as locationsApi from "../../api/locations";
import * as rolesApi from "../../api/roles";
import * as shiftTemplatesApi from "../../api/shiftTemplates";
import {
  WEEKDAY_NAMES,
  WEEKEND_NAMES,
  blocksToScheduleJson,
  scheduleJsonToBlocks,
} from "../../components/shared/ShiftCalendar";
import type { CondensedRole, Location, Role, ShiftTemplate } from "../../types";

// ── types ──

interface ShiftEntry {
  id: string;
  day: string;
  role_id: string;
  role_name: string;
  headcount: number;
  start_time: string; // "HH:MM"
  end_time: string; // "HH:MM"  (if < start_time, means next day)
}

const ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const WEEKDAY_WEEKEND_DAYS = ["Weekday", "Weekend"];

let _nextId = 1;
function nextId(): string {
  return `se-${_nextId++}`;
}

// ── helpers ──

function formatTime(t: string): string {
  const [h, m] = t.split(":").map(Number);
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${h12}:${m.toString().padStart(2, "0")} ${ampm}`;
}

function isOvernight(start: string, end: string): boolean {
  return end <= start && end !== "00:00";
}

// ── Color palette for roles ──
const ROLE_COLORS = [
  "bg-blue-100 text-blue-800 border-blue-300",
  "bg-emerald-100 text-emerald-800 border-emerald-300",
  "bg-purple-100 text-purple-800 border-purple-300",
  "bg-orange-100 text-orange-800 border-orange-300",
  "bg-pink-100 text-pink-800 border-pink-300",
  "bg-cyan-100 text-cyan-800 border-cyan-300",
  "bg-yellow-100 text-yellow-800 border-yellow-300",
  "bg-red-100 text-red-800 border-red-300",
];

// ── Shift List Component ──

function ShiftList({
  shifts,
  days,
  roleColorMap,
  onEdit,
  onDelete,
  readonly,
}: {
  shifts: ShiftEntry[];
  days: string[];
  roleColorMap: Map<string, string>;
  onEdit?: (shift: ShiftEntry) => void;
  onDelete?: (id: string) => void;
  readonly?: boolean;
}) {
  // Only show days that have shifts
  const daysWithShifts = days.filter((day) =>
    shifts.some((s) => s.day === day)
  );

  if (shifts.length === 0) {
    return (
      <p className="text-sm text-gray-400 italic py-2">No shifts added yet.</p>
    );
  }

  return (
    <div className="space-y-3">
      {daysWithShifts.map((day) => {
        const dayShifts = shifts.filter((s) => s.day === day);
        // Only show roles that have shifts for this day
        const rolesInDay = [...new Set(dayShifts.map((s) => s.role_name))];

        return (
          <div key={day} className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
              <span className="text-sm font-semibold text-gray-700">{day}</span>
              <span className="text-xs text-gray-400 ml-2">
                {dayShifts.length} shift{dayShifts.length !== 1 ? "s" : ""}
              </span>
            </div>
            <div className="divide-y divide-gray-100">
              {rolesInDay.map((roleName) => {
                const roleShifts = dayShifts.filter((s) => s.role_name === roleName);
                const colorClass = roleColorMap.get(roleShifts[0]?.role_id ?? "") ?? ROLE_COLORS[0];

                return roleShifts.map((shift) => (
                  <div
                    key={shift.id}
                    className="flex items-center gap-3 px-4 py-2 hover:bg-gray-50"
                  >
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${colorClass}`}
                    >
                      {shift.role_name}
                    </span>
                    <span className="text-sm text-gray-700 font-medium">
                      {formatTime(shift.start_time)} - {formatTime(shift.end_time)}
                      {isOvernight(shift.start_time, shift.end_time) && (
                        <span className="text-xs text-amber-600 ml-1">(+1 day)</span>
                      )}
                    </span>
                    <span className="text-xs text-gray-500">
                      x{shift.headcount}
                    </span>
                    {!readonly && (
                      <div className="ml-auto flex gap-2">
                        <button
                          onClick={() => onEdit?.(shift)}
                          className="text-indigo-600 hover:text-indigo-800 text-xs font-medium"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => onDelete?.(shift.id)}
                          className="text-red-500 hover:text-red-700 text-xs font-medium"
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </div>
                ));
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Add/Edit Shift Form ──

function ShiftForm({
  roles,
  days,
  initial,
  onSave,
  onCancel,
}: {
  roles: Role[];
  days: string[];
  initial?: ShiftEntry | null;
  onSave: (shift: ShiftEntry) => void;
  onCancel: () => void;
}) {
  const [day, setDay] = useState(initial?.day ?? days[0] ?? "Monday");
  const [roleId, setRoleId] = useState(initial?.role_id ?? "");
  const [startTime, setStartTime] = useState(initial?.start_time ?? "09:00");
  const [endTime, setEndTime] = useState(initial?.end_time ?? "17:00");
  const [headcount, setHeadcount] = useState(initial?.headcount ?? 1);

  const selectedRole = roles.find((r) => r.id === roleId);

  const handleSubmit = () => {
    if (!roleId || !selectedRole) return;
    onSave({
      id: initial?.id ?? nextId(),
      day,
      role_id: roleId,
      role_name: selectedRole.name,
      headcount,
      start_time: startTime,
      end_time: endTime,
    });
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Day</label>
          <select
            value={day}
            onChange={(e) => setDay(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          >
            {days.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Role</label>
          <select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          >
            <option value="">-- select role --</option>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Headcount</label>
          <input
            type="number"
            min={1}
            max={50}
            value={headcount}
            onChange={(e) => setHeadcount(Number(e.target.value))}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Start Time</label>
          <input
            type="time"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">
            End Time
            {isOvernight(startTime, endTime) && (
              <span className="text-amber-600 ml-1">(next day)</span>
            )}
          </label>
          <input
            type="time"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          />
        </div>
      </div>
      <div className="flex gap-2 pt-1">
        <button
          onClick={handleSubmit}
          disabled={!roleId}
          className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium"
        >
          {initial ? "Update Shift" : "Add Shift"}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Shift Editor (used in both add and edit template flows) ──

function ShiftEditor({
  roles,
  shifts,
  onChange,
  mode,
  onExpandToEveryDay,
}: {
  roles: Role[];
  shifts: ShiftEntry[];
  onChange: (shifts: ShiftEntry[]) => void;
  mode: "weekday-weekend" | "every-day";
  onExpandToEveryDay?: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingShift, setEditingShift] = useState<ShiftEntry | null>(null);

  const days = mode === "weekday-weekend" ? WEEKDAY_WEEKEND_DAYS : ALL_DAYS;

  const roleColorMap = useMemo(() => {
    const map = new Map<string, string>();
    const usedRoleIds = [...new Set(shifts.map((s) => s.role_id))];
    usedRoleIds.forEach((rid, i) => map.set(rid, ROLE_COLORS[i % ROLE_COLORS.length]));
    // Also map roles not yet used (for the form preview)
    roles.forEach((r) => {
      if (!map.has(r.id)) map.set(r.id, ROLE_COLORS[map.size % ROLE_COLORS.length]);
    });
    return map;
  }, [shifts, roles]);

  const handleAddShift = (shift: ShiftEntry) => {
    onChange([...shifts, shift]);
    setShowForm(false);
  };

  const handleUpdateShift = (updated: ShiftEntry) => {
    onChange(shifts.map((s) => (s.id === updated.id ? updated : s)));
    setEditingShift(null);
  };

  const handleDeleteShift = (id: string) => {
    onChange(shifts.filter((s) => s.id !== id));
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-gray-700">
          Weekly Schedule
          <span className="ml-2 text-xs font-normal text-gray-400">
            ({mode === "weekday-weekend" ? "Weekday / Weekend" : "Every Day"})
          </span>
        </span>
        {mode === "weekday-weekend" && onExpandToEveryDay && (
          <button
            onClick={onExpandToEveryDay}
            className="px-3 py-1.5 bg-indigo-100 text-indigo-700 rounded hover:bg-indigo-200 text-xs font-medium"
          >
            Expand to Every Day
          </button>
        )}
        {!showForm && !editingShift && (
          <button
            onClick={() => setShowForm(true)}
            className="px-3 py-1.5 bg-green-600 text-white rounded hover:bg-green-700 text-xs font-medium"
          >
            + Add Shift
          </button>
        )}
      </div>

      <ShiftList
        shifts={shifts}
        days={days}
        roleColorMap={roleColorMap}
        onEdit={(s) => {
          setEditingShift(s);
          setShowForm(false);
        }}
        onDelete={handleDeleteShift}
      />

      {showForm && (
        <ShiftForm
          roles={roles}
          days={days}
          onSave={handleAddShift}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingShift && (
        <ShiftForm
          roles={roles}
          days={days}
          initial={editingShift}
          onSave={handleUpdateShift}
          onCancel={() => setEditingShift(null)}
        />
      )}
    </div>
  );
}

// ── Read-only shift display for template list ──

function ShiftDisplay({ shifts }: { shifts: ShiftEntry[] }) {
  const days = ALL_DAYS;
  const roleColorMap = useMemo(() => {
    const map = new Map<string, string>();
    const usedRoleIds = [...new Set(shifts.map((s) => s.role_id))];
    usedRoleIds.forEach((rid, i) => map.set(rid, ROLE_COLORS[i % ROLE_COLORS.length]));
    return map;
  }, [shifts]);

  return (
    <ShiftList shifts={shifts} days={days} roleColorMap={roleColorMap} readonly />
  );
}

// ── Main Page ──

function entriesToBlocks(entries: ShiftEntry[]) {
  return entries.map((e) => ({
    id: e.id,
    day: e.day,
    role_id: e.role_id,
    role_name: e.role_name,
    headcount: e.headcount,
    start_time: e.start_time,
    end_time: e.end_time,
  }));
}

function blocksToEntries(
  blocks: ReturnType<typeof scheduleJsonToBlocks>
): ShiftEntry[] {
  return blocks.map((b) => ({
    id: b.id || nextId(),
    day: b.day,
    role_id: b.role_id,
    role_name: b.role_name,
    headcount: b.headcount,
    start_time: b.start_time,
    end_time: b.end_time,
  }));
}

export default function ShiftTemplates() {
  const [templates, setTemplates] = useState<ShiftTemplate[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [condensedRoles, setCondensedRoles] = useState<CondensedRole[]>([]);
  const [error, setError] = useState("");

  // Edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editLocationId, setEditLocationId] = useState("");
  const [editShifts, setEditShifts] = useState<ShiftEntry[]>([]);

  // Add state
  const [showAdd, setShowAdd] = useState(false);
  const [addMode, setAddMode] = useState<"choose" | "weekday-weekend" | "every-day">("choose");
  const [addName, setAddName] = useState("");
  const [addLocationId, setAddLocationId] = useState("");
  const [addShifts, setAddShifts] = useState<ShiftEntry[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const [tmpl, locs, rls, crs] = await Promise.all([
        shiftTemplatesApi.listShiftTemplates(),
        locationsApi.listLocations(),
        rolesApi.listRoles(),
        condensedRolesApi.listCondensedRoles(),
      ]);
      setTemplates(tmpl);
      setLocations(locs);
      setRoles(rls);
      setCondensedRoles(crs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const effectiveRoles: Role[] = useMemo(() => {
    const groupedRoleIds = new Set(
      condensedRoles.flatMap((cr) => cr.roles.map((r) => r.role_id))
    );
    const condensed: Role[] = condensedRoles.map((cr) => ({
      id: cr.id,
      company_id: cr.company_id,
      name: cr.name,
      description: null,
      external_id: null,
    }));
    const ungrouped = roles.filter((r) => !groupedRoleIds.has(r.id));
    return [...condensed, ...ungrouped].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
    );
  }, [roles, condensedRoles]);

  const locMap = useMemo(() => {
    const m = new Map<string, string>();
    locations.forEach((l) => m.set(l.id, l.name));
    return m;
  }, [locations]);

  const startEdit = (t: ShiftTemplate) => {
    setEditingId(t.id);
    setEditName(t.name);
    setEditLocationId(t.location_id);
    setEditShifts(
      blocksToEntries(
        scheduleJsonToBlocks(t.weekly_schedule as Record<string, unknown>[])
      )
    );
  };

  const cancelEdit = () => {
    setEditingId(null);
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await shiftTemplatesApi.updateShiftTemplate(editingId, {
        name: editName,
        weekly_schedule: blocksToScheduleJson(entriesToBlocks(editShifts)),
      });
      setEditingId(null);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await shiftTemplatesApi.deleteShiftTemplate(id);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const expandShifts = (shifts: ShiftEntry[]): ShiftEntry[] => {
    const expanded: ShiftEntry[] = [];
    for (const shift of shifts) {
      if (shift.day === "Weekday") {
        for (const dayName of WEEKDAY_NAMES) {
          expanded.push({ ...shift, id: nextId(), day: dayName });
        }
      } else if (shift.day === "Weekend") {
        for (const dayName of WEEKEND_NAMES) {
          expanded.push({ ...shift, id: nextId(), day: dayName });
        }
      } else {
        expanded.push(shift);
      }
    }
    return expanded;
  };

  const handleExpandToEveryDay = () => {
    setAddShifts(expandShifts(addShifts));
    setAddMode("every-day");
  };

  const handleAdd = async () => {
    if (!addLocationId || !addName) {
      setError("Name and location are required");
      return;
    }
    try {
      const finalShifts = expandShifts(addShifts);
      await shiftTemplatesApi.createShiftTemplate({
        location_id: addLocationId,
        name: addName,
        weekly_schedule: blocksToScheduleJson(entriesToBlocks(finalShifts)),
      });
      setShowAdd(false);
      setAddMode("choose");
      setAddName("");
      setAddLocationId("");
      setAddShifts([]);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  return (
    <div>
      <div className="relative flex items-center mb-6">
        <h1 className="text-2xl font-bold">Shift Templates</h1>
        {!showAdd && (
          <button
            onClick={() => setShowAdd(true)}
            className="ml-6 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
          >
            + Add Template
          </button>
        )}
      </div>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
          <button
            onClick={() => setError("")}
            className="ml-2 text-red-500 hover:text-red-700"
          >
            dismiss
          </button>
        </div>
      )}

      {/* Add form */}
      {showAdd && (
        <div className="bg-green-50 rounded-lg shadow p-6 mb-6 space-y-4">
          <h2 className="text-lg font-semibold">New Shift Template</h2>

          {/* Step 1: Choose mode */}
          {addMode === "choose" && (
            <div className="space-y-3">
              <p className="text-sm text-gray-600">Choose a schedule layout:</p>
              <div className="flex gap-4 max-w-[600px]">
                <button
                  onClick={() => setAddMode("weekday-weekend")}
                  className="flex-1 p-4 bg-white border-2 border-gray-200 rounded-lg hover:border-indigo-400 transition-colors text-left"
                >
                  <div className="font-semibold text-gray-800 mb-1">Weekday &amp; Weekend</div>
                  <p className="text-xs text-gray-500">
                    Define shifts for weekdays and weekends separately. Can expand to every day later.
                  </p>
                </button>
                <button
                  onClick={() => setAddMode("every-day")}
                  className="flex-1 p-4 bg-white border-2 border-gray-200 rounded-lg hover:border-indigo-400 transition-colors text-left"
                >
                  <div className="font-semibold text-gray-800 mb-1">Every Day</div>
                  <p className="text-xs text-gray-500">
                    Define shifts individually for each day of the week (Mon-Sun).
                  </p>
                </button>
              </div>
              <button
                onClick={() => {
                  setShowAdd(false);
                  setAddMode("choose");
                  setAddShifts([]);
                }}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Step 2: Form-based editor */}
          {addMode !== "choose" && (
            <>
              <div className="flex gap-4 flex-wrap">
                <div style={{ maxWidth: 450 }} className="flex-1 min-w-[200px]">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                  <input
                    type="text"
                    value={addName}
                    onChange={(e) => setAddName(e.target.value)}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                    placeholder="e.g. Weekday Standard"
                  />
                </div>
                <div style={{ maxWidth: 500 }} className="flex-1 min-w-[200px]">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Location</label>
                  <select
                    value={addLocationId}
                    onChange={(e) => setAddLocationId(e.target.value)}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                  >
                    <option value="">-- select --</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>{l.name}</option>
                    ))}
                  </select>
                </div>
              </div>

              <ShiftEditor
                roles={effectiveRoles}
                shifts={addShifts}
                onChange={setAddShifts}
                mode={addMode}
                onExpandToEveryDay={addMode === "weekday-weekend" ? handleExpandToEveryDay : undefined}
              />

              <div className="flex gap-2">
                <button
                  onClick={handleAdd}
                  className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
                >
                  Create
                </button>
                <button
                  onClick={() => {
                    setShowAdd(false);
                    setAddMode("choose");
                    setAddShifts([]);
                    setAddName("");
                    setAddLocationId("");
                  }}
                  className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                >
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Templates list */}
      <div className="space-y-4">
        {templates.map((t) => {
          const isEditing = editingId === t.id;
          const displayShifts = blocksToEntries(
            scheduleJsonToBlocks(t.weekly_schedule as Record<string, unknown>[])
          );

          return (
            <div key={t.id} className="bg-white rounded-lg shadow p-6">
              {isEditing ? (
                <div className="space-y-4">
                  <div className="flex gap-4 flex-wrap">
                    <div style={{ maxWidth: 450 }} className="flex-1 min-w-[200px]">
                      <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                      />
                    </div>
                    <div style={{ maxWidth: 500 }} className="flex-1 min-w-[200px]">
                      <label className="block text-sm font-medium text-gray-700 mb-1">Location</label>
                      <input
                        type="text"
                        disabled
                        value={locMap.get(editLocationId) ?? editLocationId}
                        className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-gray-50 text-gray-500"
                      />
                    </div>
                  </div>

                  <ShiftEditor
                    roles={effectiveRoles}
                    shifts={editShifts}
                    onChange={setEditShifts}
                    mode="every-day"
                  />

                  <div className="flex gap-2">
                    <button
                      onClick={saveEdit}
                      className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
                    >
                      Save
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-center mb-3 gap-4">
                    <div>
                      <h3 className="text-lg font-semibold">{t.name}</h3>
                      <p className="text-sm text-gray-500">
                        Location: {locMap.get(t.location_id) ?? t.location_id}
                      </p>
                    </div>
                    <div className="flex gap-2 ml-auto">
                      <button
                        onClick={() => startEdit(t)}
                        className="text-indigo-600 hover:text-indigo-800 text-sm font-medium"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(t.id)}
                        className="text-red-600 hover:text-red-800 text-sm font-medium"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <ShiftDisplay shifts={displayShifts} />
                </div>
              )}
            </div>
          );
        })}
        {templates.length === 0 && !showAdd && (
          <p className="text-gray-500 text-sm">
            No shift templates yet. Create one above.
          </p>
        )}
      </div>
    </div>
  );
}
