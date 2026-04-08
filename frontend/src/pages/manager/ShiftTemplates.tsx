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
import DemoGuard from "../../components/shared/DemoGuard";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, action, roleColorsLight } from "../../theme";
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
const ROLE_COLORS = roleColorsLight;

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
  const { t } = useLanguage();

  // Only show days that have shifts
  const daysWithShifts = days.filter((day) =>
    shifts.some((s) => s.day === day)
  );

  if (shifts.length === 0) {
    return (
      <p className={`text-sm ${text.muted} italic py-2`}>{t.shiftTemplates.noShiftsYet}</p>
    );
  }

  return (
    <div className="space-y-3">
      {daysWithShifts.map((day) => {
        const dayShifts = shifts.filter((s) => s.day === day);
        // Only show roles that have shifts for this day
        const rolesInDay = [...new Set(dayShifts.map((s) => s.role_name))];

        return (
          <div key={day} className={`border ${border.default} rounded-lg overflow-hidden`}>
            <div className={`${bg.tableHeader} px-4 py-2 border-b ${border.default}`}>
              <span className="text-sm font-semibold text-gray-600">{day}</span>
              <span className={`text-xs ${text.muted} ml-2`}>
                {dayShifts.length} {dayShifts.length !== 1 ? t.common.shifts : t.common.shift}
              </span>
            </div>
            <div className={`divide-y ${border.divider}`}>
              {rolesInDay.map((roleName) => {
                const roleShifts = dayShifts.filter((s) => s.role_name === roleName);
                const colorClass = roleColorMap.get(roleShifts[0]?.role_id ?? "") ?? ROLE_COLORS[0];

                return roleShifts.map((shift) => (
                  <div
                    key={shift.id}
                    className={`flex items-center gap-3 px-4 py-2 ${bg.rowHover}`}
                  >
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${colorClass}`}
                    >
                      {shift.role_name}
                    </span>
                    <span className="text-sm text-gray-600 font-medium">
                      {formatTime(shift.start_time)} - {formatTime(shift.end_time)}
                      {isOvernight(shift.start_time, shift.end_time) && (
                        <span className="text-xs text-amber-400 ml-1">(+1 {t.common.day})</span>
                      )}
                    </span>
                    <span className={`text-xs ${text.muted}`}>
                      x{shift.headcount}
                    </span>
                    {!readonly && (
                      <div className="ml-auto flex gap-2">
                        <button
                          onClick={() => onEdit?.(shift)}
                          className={action.edit}
                        >
                          {t.common.edit}
                        </button>
                        <button
                          onClick={() => onDelete?.(shift.id)}
                          className={action.delete}
                        >
                          {t.common.remove}
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
  const { t } = useLanguage();
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
    <div className={`glass-card p-4 border ${border.default}`}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <label className="glass-label-sm">{t.common.day}</label>
          <select
            value={day}
            onChange={(e) => setDay(e.target.value)}
            className="glass-input w-full"
          >
            {days.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="glass-label-sm">{t.common.role}</label>
          <select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            className="glass-input w-full"
          >
            <option value="">{t.shiftTemplates.selectRole}</option>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="glass-label-sm">{t.shiftTemplates.headcount}</label>
          <input
            type="number"
            min={1}
            max={50}
            value={headcount}
            onChange={(e) => setHeadcount(Number(e.target.value))}
            className="glass-input w-full"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 mt-3">
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
          <label className="glass-label-sm">
            {t.common.endTime}
            {isOvernight(startTime, endTime) && (
              <span className="text-amber-400 ml-1">{t.shiftTemplates.nextDay}</span>
            )}
          </label>
          <input
            type="time"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="glass-input w-full"
          />
        </div>
      </div>
      <div className="flex gap-2 pt-3">
        <button
          onClick={handleSubmit}
          disabled={!roleId}
          className="glass-btn-primary disabled:opacity-50"
        >
          {initial ? t.shiftTemplates.updateShift : t.shiftTemplates.addShiftBtn}
        </button>
        <button
          onClick={onCancel}
          className="glass-btn-secondary"
        >
          {t.common.cancel}
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
  const { t } = useLanguage();
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
        <span className={`text-sm font-medium ${text.secondary}`}>
          {t.shiftTemplates.weeklySchedule}
          <span className="ml-2 text-xs font-normal text-gray-500">
            ({mode === "weekday-weekend" ? t.shiftTemplates.weekdayWeekendMode : t.shiftTemplates.everyDayMode})
          </span>
        </span>
        {mode === "weekday-weekend" && onExpandToEveryDay && (
          <button
            onClick={onExpandToEveryDay}
            className="px-3 py-1.5 bg-accent/10 text-accent-dark rounded hover:bg-accent/20 text-xs font-medium transition-colors"
          >
            {t.shiftTemplates.expandToEveryDay}
          </button>
        )}
        {!showForm && !editingShift && (
          <button
            onClick={() => setShowForm(true)}
            className="glass-btn-success px-3 py-1.5 text-xs"
          >
            {t.shiftTemplates.addShift}
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
  const { t } = useLanguage();
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
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.shiftTemplates.title}</h1>
        {!showAdd && (
          <DemoGuard>
            <button
              onClick={() => setShowAdd(true)}
              className="glass-btn-primary ml-6"
            >
              {t.shiftTemplates.addTemplate}
            </button>
          </DemoGuard>
        )}
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

      {/* Add form */}
      {showAdd && (
        <div className={`${bg.addForm} rounded-xl p-6 mb-6 space-y-4`}>
          <h2 className={`text-lg font-semibold ${text.heading}`}>{t.shiftTemplates.newTemplate}</h2>

          {/* Step 1: Choose mode */}
          {addMode === "choose" && (
            <div className="space-y-3">
              <p className={`text-sm ${text.muted}`}>{t.shiftTemplates.chooseLayout}</p>
              <div className="flex gap-4 max-w-[600px]">
                <button
                  onClick={() => setAddMode("weekday-weekend")}
                  className={`flex-1 p-4 bg-white/50 border-2 ${border.default} rounded-xl hover:border-accent/30 transition-colors text-left`}
                >
                  <div className="font-semibold text-gray-900 mb-1">{t.shiftTemplates.weekdayWeekend}</div>
                  <p className={`text-xs ${text.muted}`}>
                    {t.shiftTemplates.weekdayWeekendDesc}
                  </p>
                </button>
                <button
                  onClick={() => setAddMode("every-day")}
                  className={`flex-1 p-4 bg-white/50 border-2 ${border.default} rounded-xl hover:border-accent/30 transition-colors text-left`}
                >
                  <div className="font-semibold text-gray-900 mb-1">{t.shiftTemplates.everyDay}</div>
                  <p className={`text-xs ${text.muted}`}>
                    {t.shiftTemplates.everyDayDesc}
                  </p>
                </button>
              </div>
              <button
                onClick={() => {
                  setShowAdd(false);
                  setAddMode("choose");
                  setAddShifts([]);
                }}
                className="glass-btn-secondary"
              >
                {t.common.cancel}
              </button>
            </div>
          )}

          {/* Step 2: Form-based editor */}
          {addMode !== "choose" && (
            <>
              <div className="flex gap-4 flex-wrap">
                <div style={{ maxWidth: 450 }} className="flex-1 min-w-[200px]">
                  <label className="glass-label-sm">{t.common.name}</label>
                  <input
                    type="text"
                    value={addName}
                    onChange={(e) => setAddName(e.target.value)}
                    className="glass-input w-full"
                    placeholder={t.shiftTemplates.namePlaceholder}
                  />
                </div>
                <div style={{ maxWidth: 500 }} className="flex-1 min-w-[200px]">
                  <label className="glass-label-sm">{t.common.location}</label>
                  <select
                    value={addLocationId}
                    onChange={(e) => setAddLocationId(e.target.value)}
                    className="glass-input w-full"
                  >
                    <option value="">{t.common.select}</option>
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
                  className="glass-btn-success"
                >
                  {t.common.create}
                </button>
                <button
                  onClick={() => {
                    setShowAdd(false);
                    setAddMode("choose");
                    setAddShifts([]);
                    setAddName("");
                    setAddLocationId("");
                  }}
                  className="glass-btn-secondary"
                >
                  {t.common.cancel}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Templates list */}
      <div className="space-y-4">
        {templates.map((tmpl) => {
          const isEditing = editingId === tmpl.id;
          const displayShifts = blocksToEntries(
            scheduleJsonToBlocks(tmpl.weekly_schedule as Record<string, unknown>[])
          );

          return (
            <div key={tmpl.id} className="glass-card p-6">
              {isEditing ? (
                <div className="space-y-4">
                  <div className="flex gap-4 flex-wrap">
                    <div style={{ maxWidth: 450 }} className="flex-1 min-w-[200px]">
                      <label className="glass-label-sm">{t.common.name}</label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="glass-input w-full"
                      />
                    </div>
                    <div style={{ maxWidth: 500 }} className="flex-1 min-w-[200px]">
                      <label className="glass-label-sm">{t.common.location}</label>
                      <input
                        type="text"
                        disabled
                        value={locMap.get(editLocationId) ?? editLocationId}
                        className={`w-full bg-sage/[0.05] ${text.muted} ${border.subtle} border rounded px-3 py-2 text-sm`}
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
                      className="glass-btn-success"
                    >
                      {t.common.save}
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="glass-btn-secondary"
                    >
                      {t.common.cancel}
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-center mb-3 gap-4">
                    <div>
                      <h3 className={`text-lg font-semibold ${text.heading}`}>{tmpl.name}</h3>
                      <p className={`text-sm ${text.muted}`}>
                        {t.shiftTemplates.locationLabel} {locMap.get(tmpl.location_id) ?? tmpl.location_id}
                      </p>
                    </div>
                    <div className="flex gap-2 ml-auto">
                      <DemoGuard>
                        <button
                          onClick={() => startEdit(tmpl)}
                          className={action.edit}
                        >
                          {t.common.edit}
                        </button>
                      </DemoGuard>
                      <DemoGuard>
                        <button
                          onClick={() => handleDelete(tmpl.id)}
                          className={action.delete}
                        >
                          {t.common.delete}
                        </button>
                      </DemoGuard>
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
            {t.shiftTemplates.noTemplatesYet}
          </p>
        )}
      </div>
    </div>
  );
}
