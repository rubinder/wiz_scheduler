import { useCallback, useEffect, useMemo, useState } from "react";
import * as locationsApi from "../../api/locations";
import * as rolesApi from "../../api/roles";
import * as shiftTemplatesApi from "../../api/shiftTemplates";
import ShiftCalendar, {
  ALL_DAYS,
  WEEKDAY_WEEKEND_DAYS,
  WEEKDAY_NAMES,
  WEEKEND_NAMES,
  blocksToScheduleJson,
  scheduleJsonToBlocks,
} from "../../components/shared/ShiftCalendar";
import type { Location, Role, ShiftTemplate } from "../../types";

export default function ShiftTemplates() {
  const [templates, setTemplates] = useState<ShiftTemplate[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState("");

  // Edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editLocationId, setEditLocationId] = useState("");
  const [editBlocks, setEditBlocks] = useState<
    ReturnType<typeof scheduleJsonToBlocks>
  >([]);

  // Add state
  const [showAdd, setShowAdd] = useState(false);
  const [addMode, setAddMode] = useState<"choose" | "weekday-weekend" | "every-day">("choose");
  const [addName, setAddName] = useState("");
  const [addLocationId, setAddLocationId] = useState("");
  const [addBlocks, setAddBlocks] = useState<
    ReturnType<typeof scheduleJsonToBlocks>
  >([]);

  const fetchData = useCallback(async () => {
    try {
      const [tmpl, locs, rls] = await Promise.all([
        shiftTemplatesApi.listShiftTemplates(),
        locationsApi.listLocations(),
        rolesApi.listRoles(),
      ]);
      setTemplates(tmpl);
      setLocations(locs);
      setRoles(rls);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const locMap = useMemo(() => {
    const m = new Map<string, string>();
    locations.forEach((l) => m.set(l.id, l.name));
    return m;
  }, [locations]);

  const startEdit = (t: ShiftTemplate) => {
    setEditingId(t.id);
    setEditName(t.name);
    setEditLocationId(t.location_id);
    setEditBlocks(
      scheduleJsonToBlocks(t.weekly_schedule as Record<string, unknown>[])
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
        weekly_schedule: blocksToScheduleJson(editBlocks),
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

  // Expand Weekday/Weekend blocks into individual day blocks
  const expandBlocks = (
    blocks: ReturnType<typeof scheduleJsonToBlocks>
  ): ReturnType<typeof scheduleJsonToBlocks> => {
    const expanded: ReturnType<typeof scheduleJsonToBlocks> = [];
    let id = Date.now();
    for (const block of blocks) {
      if (block.day === "Weekday") {
        for (const dayName of WEEKDAY_NAMES) {
          expanded.push({ ...block, id: `exp-${id++}`, day: dayName });
        }
      } else if (block.day === "Weekend") {
        for (const dayName of WEEKEND_NAMES) {
          expanded.push({ ...block, id: `exp-${id++}`, day: dayName });
        }
      } else {
        expanded.push(block);
      }
    }
    return expanded;
  };

  const handleExpandToEveryDay = () => {
    setAddBlocks(expandBlocks(addBlocks));
    setAddMode("every-day");
  };

  const handleAdd = async () => {
    if (!addLocationId || !addName) {
      setError("Name and location are required");
      return;
    }
    try {
      // Always expand Weekday/Weekend to real days before saving
      const finalBlocks = expandBlocks(addBlocks);
      await shiftTemplatesApi.createShiftTemplate({
        location_id: addLocationId,
        name: addName,
        weekly_schedule: blocksToScheduleJson(finalBlocks),
      });
      setShowAdd(false);
      setAddMode("choose");
      setAddName("");
      setAddLocationId("");
      setAddBlocks([]);
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
            className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
            style={{ position: "absolute", left: 270 }}
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
              <p className="text-sm text-gray-600">
                Choose a schedule layout:
              </p>
              <div className="flex gap-4 max-w-[600px]">
                <button
                  onClick={() => setAddMode("weekday-weekend")}
                  className="flex-1 p-4 bg-white border-2 border-gray-200 rounded-lg hover:border-indigo-400 transition-colors text-left"
                >
                  <div className="font-semibold text-gray-800 mb-1">
                    Weekday &amp; Weekend
                  </div>
                  <p className="text-xs text-gray-500">
                    Define shifts for weekdays and weekends separately. Can
                    expand to every day later.
                  </p>
                </button>
                <button
                  onClick={() => setAddMode("every-day")}
                  className="flex-1 p-4 bg-white border-2 border-gray-200 rounded-lg hover:border-indigo-400 transition-colors text-left"
                >
                  <div className="font-semibold text-gray-800 mb-1">
                    Every Day
                  </div>
                  <p className="text-xs text-gray-500">
                    Define shifts individually for each day of the week
                    (Mon-Sun).
                  </p>
                </button>
              </div>
              <button
                onClick={() => {
                  setShowAdd(false);
                  setAddMode("choose");
                  setAddBlocks([]);
                }}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Step 2: Calendar editor */}
          {addMode !== "choose" && (
            <>
              <div className="flex gap-4 flex-wrap">
                <div
                  style={{ maxWidth: 450 }}
                  className="flex-1 min-w-[200px]"
                >
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Name
                  </label>
                  <input
                    type="text"
                    value={addName}
                    onChange={(e) => setAddName(e.target.value)}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                    placeholder="e.g. Weekday Standard"
                  />
                </div>
                <div
                  style={{ maxWidth: 500 }}
                  className="flex-1 min-w-[200px]"
                >
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Location
                  </label>
                  <select
                    value={addLocationId}
                    onChange={(e) => setAddLocationId(e.target.value)}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                  >
                    <option value="">-- select --</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <div className="mb-2">
                  <div className="flex items-center gap-3">
                    <label className="text-sm font-medium text-gray-700">
                      Weekly Schedule
                      <span className="ml-2 text-xs font-normal text-gray-400">
                        {addMode === "weekday-weekend"
                          ? "(Weekday / Weekend)"
                          : "(Every Day)"}
                      </span>
                    </label>
                    {addMode === "weekday-weekend" && (
                      <button
                        onClick={handleExpandToEveryDay}
                        className="px-3 py-1.5 bg-indigo-100 text-indigo-700 rounded hover:bg-indigo-200 text-xs font-medium whitespace-nowrap"
                      >
                        Expand to Every Day
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Click and drag on a role column to create a shift block.
                    Click a block to edit headcount and times.
                  </p>
                </div>
                <ShiftCalendar
                  roles={roles}
                  value={addBlocks}
                  onChange={setAddBlocks}
                  days={
                    addMode === "weekday-weekend"
                      ? WEEKDAY_WEEKEND_DAYS
                      : ALL_DAYS
                  }
                />
              </div>

              {addBlocks.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-gray-500">
                    Generated JSON ({addBlocks.length} blocks)
                  </summary>
                  <pre className="mt-1 bg-gray-100 rounded p-2 overflow-x-auto">
                    {JSON.stringify(
                      blocksToScheduleJson(expandBlocks(addBlocks)),
                      null,
                      2
                    )}
                  </pre>
                </details>
              )}

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
                    setAddBlocks([]);
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
          return (
            <div key={t.id} className="bg-white rounded-lg shadow p-6">
              {isEditing ? (
                <div className="space-y-4">
                  <div className="flex gap-4 flex-wrap">
                    <div style={{ maxWidth: 450 }} className="flex-1 min-w-[200px]">
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Name
                      </label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                      />
                    </div>
                    <div style={{ maxWidth: 500 }} className="flex-1 min-w-[200px]">
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Location
                      </label>
                      <input
                        type="text"
                        disabled
                        value={locMap.get(editLocationId) ?? editLocationId}
                        className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-gray-50 text-gray-500"
                      />
                    </div>
                  </div>
                  <ShiftCalendar
                    roles={roles}
                    value={editBlocks}
                    onChange={setEditBlocks}
                  />
                  {editBlocks.length > 0 && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-gray-500">
                        Generated JSON ({editBlocks.length} blocks)
                      </summary>
                      <pre className="mt-1 bg-gray-100 rounded p-2 overflow-x-auto">
                        {JSON.stringify(
                          blocksToScheduleJson(editBlocks),
                          null,
                          2
                        )}
                      </pre>
                    </details>
                  )}
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
                  <div className="relative flex items-center mb-3">
                    <div>
                      <h3 className="text-lg font-semibold">{t.name}</h3>
                      <p className="text-sm text-gray-500">
                        Location:{" "}
                        {locMap.get(t.location_id) ?? t.location_id}
                      </p>
                    </div>
                    <div className="flex gap-2" style={{ position: "absolute", left: 246 }}>
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
                  {/* Visual summary */}
                  <ShiftCalendar
                    roles={roles}
                    value={scheduleJsonToBlocks(
                      t.weekly_schedule as Record<string, unknown>[]
                    )}
                    onChange={() => {}}
                  />
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
