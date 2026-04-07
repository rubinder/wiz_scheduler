import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as schedulesApi from "../../api/schedules";
import * as shiftTemplatesApi from "../../api/shiftTemplates";
import * as locationsApi from "../../api/locations";
import { listEmployees } from "../../api/employees";
import * as billingApi from "../../api/billing";
import type { AiCreditStatus, ScheduleQuota } from "../../api/billing";
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import StatusBadge from "../../components/shared/StatusBadge";
import DemoGuard from "../../components/shared/DemoGuard";
import { useScheduleStream } from "../../hooks/useScheduleStream";
import { useLanguage } from "../../i18n/LanguageContext";
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
  "bg-blue-500/15 border-blue-400/20 text-blue-300",
  "bg-emerald-500/15 border-emerald-400/20 text-emerald-300",
  "bg-purple-500/15 border-purple-400/20 text-purple-300",
  "bg-amber-500/15 border-amber-400/20 text-amber-300",
  "bg-rose-500/15 border-rose-400/20 text-rose-300",
  "bg-cyan-500/15 border-cyan-400/20 text-cyan-300",
  "bg-indigo-500/15 border-indigo-400/20 text-indigo-300",
  "bg-orange-500/15 border-orange-400/20 text-orange-300",
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
  const { t } = useLanguage();
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
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
          <h3 className="text-lg font-semibold text-white">{t.schedule.editShift}</h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="glass-label">
              {t.common.employee}
            </label>
            <EmployeeSearchBox
              employees={employees}
              value={employeeId}
              onChange={setEmployeeId}
              placeholder={t.schedule.searchEmployee}
            />
          </div>

          <div>
            <label className="glass-label">
              {t.common.role}
            </label>
            <select
              value={roleName}
              onChange={(e) => setRoleName(e.target.value)}
              className="glass-input w-full"
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
              <label className="glass-label">
                {t.common.startTime}
              </label>
              <input
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="glass-input w-full"
              />
            </div>
            <div>
              <label className="glass-label">
                {t.common.endTime}
              </label>
              <input
                type="time"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                className="glass-input w-full"
              />
            </div>
          </div>

          <div className="text-sm text-gray-400">
            {t.common.date}: {getDayLabel(shift.date)} ({shift.date})
          </div>
        </div>

        <div className="flex justify-between items-center px-6 py-4 border-t border-white/[0.08] bg-white/[0.03] rounded-b-2xl">
          <button
            onClick={onDelete}
            className="px-3 py-2 bg-red-500/15 text-red-300 rounded-lg hover:bg-red-500/25 text-sm font-medium"
          >
            {t.schedule.removeShift}
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="glass-btn-secondary text-sm font-medium"
            >
              {t.common.cancel}
            </button>
            <button
              onClick={handleSave}
              disabled={!employeeId}
              className="glass-btn-primary text-sm font-medium"
            >
              {t.common.save}
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
  const { t } = useLanguage();
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
          <tr className="bg-white/[0.04]">
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase sticky left-0 bg-[#0f0f23] z-10 min-w-[140px]">
              {t.common.role}
            </th>
            {dates.map((d) => (
              <th
                key={d}
                className="px-3 py-3 text-center text-xs font-semibold text-gray-400 uppercase min-w-[160px]"
              >
                {getDayLabel(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role} className="border-t border-white/[0.06]">
              <td className="px-4 py-3 text-sm font-medium text-gray-300 align-top sticky left-0 bg-[#0f0f23] z-10">
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
                      <span className="text-gray-600 text-xs">&mdash;</span>
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
                            className={`rounded-lg border px-2.5 py-1.5 ${roleColorMap[role]} ${
                              editable
                                ? "cursor-pointer hover:ring-2 hover:ring-purple-400/40 transition-shadow"
                                : ""
                            }`}
                          >
                            <div className="text-sm font-medium text-inherit">
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
  const { t } = useLanguage();
  const [searchParams, setSearchParams] = useSearchParams();
  const [startDate, setStartDate] = useState(getNextMonday());
  const [endDate, setEndDate] = useState(addDays(getNextMonday(), 6));
  const weekStart = startDate; // alias for compatibility
  const { results, isStreaming, error, generate, reset } =
    useScheduleStream();
  const [actionError, setActionError] = useState("");

  // AI credit & schedule quota state
  const [creditStatus, setCreditStatus] = useState<AiCreditStatus | null>(null);
  const [scheduleQuota, setScheduleQuota] = useState<ScheduleQuota | null>(null);
  const [showPurchaseModal, setShowPurchaseModal] = useState(false);
  const [purchaseReason, setPurchaseReason] = useState<"ai" | "schedules">("ai");
  const [purchaseAmount, setPurchaseAmount] = useState(5);
  const [purchaseLoading, setPurchaseLoading] = useState(false);
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
  const [localStrategy, setLocalStrategy] = useState<"random" | "rotation" | "rotation_history" | "max_hours">("rotation");
  const [fairnessWeight, setFairnessWeight] = useState(0.7);
  const [maxHours, setMaxHours] = useState(40);
  const [hourStrictness, setHourStrictness] = useState(0.8);

  // Fetch AI credit status and schedule quota on mount and after purchase
  const fetchCredits = useCallback(async () => {
    try {
      const [aiStatus, quota] = await Promise.all([
        billingApi.getAiCredits(),
        billingApi.getScheduleQuota(),
      ]);
      setCreditStatus(aiStatus);
      setScheduleQuota(quota);
    } catch {
      // Non-critical — don't block the page
    }
  }, []);

  useEffect(() => {
    fetchCredits();
  }, [fetchCredits]);

  // Handle return from Stripe credit purchase
  useEffect(() => {
    const creditsSessionId = searchParams.get("credits_session_id");
    if (creditsSessionId) {
      searchParams.delete("credits_session_id");
      setSearchParams(searchParams, { replace: true });
      // Confirm the purchase
      billingApi.confirmCredits(creditsSessionId).then(() => {
        fetchCredits();
      }).catch(() => {
        setActionError("Failed to confirm credit purchase");
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePurchaseCredits = async () => {
    setPurchaseLoading(true);
    try {
      const { url } = await billingApi.purchaseCredits(purchaseAmount);
      window.location.href = url;
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to start purchase");
      setPurchaseLoading(false);
    }
  };

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
    // Check schedule quota first (applies to both modes)
    if (scheduleQuota && !scheduleQuota.can_generate) {
      setPurchaseReason("schedules");
      setShowPurchaseModal(true);
      return;
    }
    // For AI mode, also check AI credits
    if (mode === "ai" && creditStatus && !creditStatus.can_generate) {
      setPurchaseReason("ai");
      setShowPurchaseModal(true);
      return;
    }
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
      ...(generateMode === "local" ? {
        useLocal: true,
        strategy: localStrategy,
        ...(localStrategy === "rotation_history" ? { strategyParam: fairnessWeight } : {}),
        ...(localStrategy === "max_hours" ? { strategyParam: maxHours, strategyParam2: hourStrictness } : {}),
      } : {}),
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
      <h1 className="text-2xl font-bold text-white mb-6">{t.schedule.title}</h1>

      {/* Credit & Quota Status Banner */}
      {(creditStatus || scheduleQuota) && (
        <div className="mb-4 space-y-2">
          {/* Schedule quota */}
          {scheduleQuota && (
            <div className={`p-3 rounded-lg border text-sm flex items-center justify-between ${
              scheduleQuota.can_generate
                ? "border-white/10 bg-white/[0.03] text-gray-400"
                : "border-red-500/30 bg-red-500/10 text-red-300"
            }`}>
              <div className="flex items-center gap-4">
                <span>
                  {t.schedule.schedulesUsed}: {scheduleQuota.schedules_used} / {scheduleQuota.schedules_free_tier}
                </span>
                {scheduleQuota.is_over_free_tier && (
                  <span className="text-xs text-gray-500">
                    ({t.schedule.scheduleFreeTierUsed})
                  </span>
                )}
              </div>
              {scheduleQuota.is_over_free_tier && !scheduleQuota.can_generate && (
                <button
                  onClick={() => { setPurchaseReason("schedules"); setShowPurchaseModal(true); }}
                  className="glass-btn-primary text-xs px-3 py-1"
                >
                  {t.schedule.buyCredits}
                </button>
              )}
            </div>
          )}
          {/* AI credits */}
          {creditStatus && (
            <div className={`p-3 rounded-lg border text-sm flex items-center justify-between ${
              creditStatus.can_generate
                ? "border-white/10 bg-white/[0.03] text-gray-400"
                : "border-red-500/30 bg-red-500/10 text-red-300"
            }`}>
              <div className="flex items-center gap-4">
                <span>
                  {t.schedule.aiCredits}:
                  {creditStatus.is_over_free_tier
                    ? ` $${creditStatus.purchased_credits_usd.toFixed(2)} ${t.schedule.purchasedRemaining}`
                    : ` $${creditStatus.free_remaining_usd.toFixed(2)} ${t.schedule.freeRemaining}`
                  }
                </span>
                {creditStatus.is_over_free_tier && (
                  <span className="text-xs text-gray-500">
                    ({t.schedule.freeTierUsed})
                  </span>
                )}
              </div>
              {creditStatus.is_over_free_tier && !creditStatus.can_generate && (
                <button
                  onClick={() => { setPurchaseReason("ai"); setShowPurchaseModal(true); }}
                  className="glass-btn-primary text-xs px-3 py-1"
                >
                  {t.schedule.buyCredits}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-4 mb-6 flex-wrap">
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium text-gray-300">{t.schedule.startLabel}</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => {
              const v = e.target.value;
              setStartDate(v);
              setEndDate(addDays(v, 6));
            }}
            className="glass-input"
          />
          <label className="text-sm font-medium text-gray-300">{t.schedule.endLabel}</label>
          <input
            type="date"
            value={endDate}
            min={startDate}
            max={addDays(startDate, 13)}
            onChange={(e) => setEndDate(e.target.value)}
            className="glass-input"
          />
          <span className="text-xs text-gray-500">
            ({daysBetween(startDate, endDate)} {daysBetween(startDate, endDate) !== 1 ? t.common.days : t.common.day})
          </span>
        </div>
        <button
          onClick={() => handleGenerateClick("local")}
          disabled={isStreaming}
          className="glass-btn-success px-5 py-3 rounded-lg font-semibold text-sm"
        >
          {isStreaming && generateMode === "local" ? t.schedule.generating : t.schedule.localGenerate}
        </button>
        <DemoGuard>
          <button
            onClick={() => handleGenerateClick("ai")}
            disabled={isStreaming}
            className="glass-btn-primary px-5 py-3 rounded-lg font-semibold text-sm"
          >
            {isStreaming && generateMode === "ai" ? t.schedule.generating : t.schedule.aiGenerate}
          </button>
        </DemoGuard>
        {results.length > 0 && !isStreaming && (
          <button
            onClick={() => {
              reset();
              setEditedShifts({});
            }}
            className="glass-btn-secondary text-sm font-medium"
          >
            {t.schedule.reset}
          </button>
        )}
      </div>

      {/* Template Picker Modal */}
      {showTemplatePicker && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-lg mx-4 max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
              <h2 className="text-lg font-semibold text-white">
                {t.schedule.selectTemplates}
              </h2>
              <button
                onClick={() => setShowTemplatePicker(false)}
                className="text-gray-500 hover:text-gray-300 text-xl leading-none"
              >
                &times;
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <p className="text-sm text-gray-400 mb-4">
                {t.schedule.selectTemplatesDesc}
              </p>

              {loadingTemplates && (
                <div className="flex items-center gap-3 py-4">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-purple-400 border-t-transparent" />
                  <span className="text-sm text-gray-400">
                    {t.schedule.loadingTemplates}
                  </span>
                </div>
              )}

              {!loadingTemplates && templates.length === 0 && (
                <p className="text-sm text-gray-500">
                  {t.schedule.noTemplatesFound}
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
                        className="mb-4 border border-white/[0.08] rounded-lg"
                      >
                        <div className="px-4 py-2 bg-white/[0.04] rounded-t-lg flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = someSelected;
                            }}
                            onChange={() => toggleAllForLocation(locId)}
                            className="rounded border-white/20 bg-white/[0.05]"
                          />
                          <span className="text-sm font-semibold text-gray-300">
                            {locationName(locId)}
                          </span>
                          <span className="text-xs text-gray-500">
                            ({locTemplates.length} {locTemplates.length !== 1 ? t.schedule.templates : t.schedule.template})
                          </span>
                        </div>
                        <div className="px-4 py-2 space-y-1">
                          {locTemplates.map((tmpl) => (
                            <label
                              key={tmpl.id}
                              className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer py-1"
                            >
                              <input
                                type="checkbox"
                                checked={selectedTemplateIds.has(tmpl.id)}
                                onChange={() => toggleTemplate(tmpl.id)}
                                className="rounded border-white/20 bg-white/[0.05]"
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
              <div className="px-6 py-3 bg-emerald-500/[0.07] border-t border-white/[0.08]">
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  {t.schedule.strategy}
                </label>
                <div className="flex gap-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="rotation"
                      checked={localStrategy === "rotation"}
                      onChange={() => setLocalStrategy("rotation")}
                      className="text-emerald-400"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-300">{t.schedule.rotation}</span>
                      <p className="text-xs text-gray-500">{t.schedule.rotationDesc}</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="random"
                      checked={localStrategy === "random"}
                      onChange={() => setLocalStrategy("random")}
                      className="text-emerald-400"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-300">{t.schedule.random}</span>
                      <p className="text-xs text-gray-500">{t.schedule.randomDesc}</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="rotation_history"
                      checked={localStrategy === "rotation_history"}
                      onChange={() => setLocalStrategy("rotation_history")}
                      className="text-emerald-400"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-300">{t.schedule.rotationHistory}</span>
                      <p className="text-xs text-gray-500">{t.schedule.rotationHistoryDesc}</p>
                    </div>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="strategy"
                      value="max_hours"
                      checked={localStrategy === "max_hours"}
                      onChange={() => setLocalStrategy("max_hours")}
                      className="text-emerald-400"
                    />
                    <div>
                      <span className="text-sm font-medium text-gray-300">{t.schedule.maxHours}</span>
                      <p className="text-xs text-gray-500">{t.schedule.maxHoursDesc}</p>
                    </div>
                  </label>
                </div>
                {localStrategy === "rotation_history" && (
                  <div className="mt-3 pt-3 border-t border-white/[0.08]">
                    <div className="flex items-center justify-between mb-2">
                      <label className="text-sm font-medium text-gray-300">{t.schedule.fairnessWeight}</label>
                      <span className="text-sm font-mono text-emerald-400">{fairnessWeight.toFixed(1)}</span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={fairnessWeight}
                      onChange={(e) => setFairnessWeight(parseFloat(e.target.value))}
                      className="w-full accent-emerald-400"
                    />
                    <div className="flex justify-between text-xs text-gray-500 mt-1">
                      <span>{t.schedule.moreRandom}</span>
                      <span>{t.schedule.moreFair}</span>
                    </div>
                  </div>
                )}
                {localStrategy === "max_hours" && (
                  <div className="mt-3 pt-3 border-t border-white/[0.08] space-y-4">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <label className="text-sm font-medium text-gray-300">{t.schedule.maxHoursLimit}</label>
                        <span className="text-sm font-mono text-emerald-400">{maxHours}h</span>
                      </div>
                      <input
                        type="range"
                        min="4"
                        max="60"
                        step="1"
                        value={maxHours}
                        onChange={(e) => setMaxHours(parseInt(e.target.value))}
                        className="w-full accent-emerald-400"
                      />
                      <div className="flex justify-between text-xs text-gray-500 mt-1">
                        <span>4h</span>
                        <span>60h</span>
                      </div>
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <label className="text-sm font-medium text-gray-300">{t.schedule.hourStrictness}</label>
                        <span className="text-sm font-mono text-emerald-400">{hourStrictness.toFixed(1)}</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.1"
                        value={hourStrictness}
                        onChange={(e) => setHourStrictness(parseFloat(e.target.value))}
                        className="w-full accent-emerald-400"
                      />
                      <div className="flex justify-between text-xs text-gray-500 mt-1">
                        <span>{t.schedule.noEnforcement}</span>
                        <span>{t.schedule.strictEnforcement}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="flex justify-between items-center px-6 py-4 border-t border-white/[0.08] bg-white/[0.03] rounded-b-2xl">
              <span className="text-xs text-gray-500">
                {selectedTemplateIds.size} {t.common.of} {templates.length} {t.common.selected}
              </span>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowTemplatePicker(false)}
                  className="glass-btn-secondary text-sm font-medium"
                >
                  {t.common.cancel}
                </button>
                <button
                  onClick={handleConfirmGenerate}
                  disabled={selectedTemplateIds.size === 0}
                  className={`${
                    generateMode === "local"
                      ? "glass-btn-success"
                      : "glass-btn-primary"
                  } text-sm font-medium disabled:opacity-50`}
                >
                  {generateMode === "local" ? t.schedule.generateLocally : t.schedule.generateWithAI} {t.schedule.forLabel} {selectedTemplateIds.size} {selectedTemplateIds.size !== 1 ? t.schedule.templates : t.schedule.template}
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
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      {actionError && (
        <div className="glass-alert-error mb-4">
          {actionError}
        </div>
      )}

      {isStreaming && results.length === 0 && (
        <div className="text-gray-400 text-sm">
          {t.schedule.waitingResults}
        </div>
      )}

      {allComplete && (
        <div className="mb-6 p-4 bg-emerald-500/10 border border-emerald-400/20 text-emerald-300 rounded-xl text-center font-semibold">
          {t.schedule.allComplete}
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
              className={`glass-card ${
                printLocationId === locationResult.location_id ? "print-target" : ""
              }`}
              data-print={printLocationId === locationResult.location_id ? "true" : undefined}
            >
              <div className="p-4 border-b border-white/[0.08] flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold text-white">
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
                      className="glass-btn-secondary px-3 py-1 text-sm font-medium print:hidden"
                    >
                      {t.schedule.print}
                    </button>
                  )}
                  {!decided && (
                    <span className="text-xs text-gray-500 mr-2">
                      {t.schedule.clickToEdit}
                    </span>
                  )}
                  {!decided && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleApprove(locationResult)}
                        disabled={saving}
                        className="glass-btn-success px-3 py-1 text-sm font-medium disabled:opacity-50"
                      >
                        {saving ? t.common.saving : t.schedule.approve}
                      </button>
                      <button
                        onClick={() => handleReject(locationResult)}
                        className="glass-btn-danger px-3 py-1 text-sm font-medium"
                      >
                        {t.schedule.reject}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {locationResult.errors.length > 0 && (
                <div className="p-4 bg-red-500/10">
                  <p className="text-sm font-medium text-red-300 mb-1">
                    {t.common.errors}:
                  </p>
                  <ul className="list-disc list-inside text-sm text-red-300">
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
                    {t.schedule.noShiftsGenerated}
                  </div>
                )}
            </div>
          );
        })}
      </div>

      {/* Purchase Credits Modal */}
      {showPurchaseModal && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-sm mx-4">
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
              <h3 className="text-lg font-semibold text-white">{t.schedule.buyAiCredits}</h3>
              <button
                onClick={() => setShowPurchaseModal(false)}
                className="text-gray-500 hover:text-gray-300 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className="text-sm text-gray-400">
                {purchaseReason === "schedules"
                  ? t.schedule.scheduleQuotaExhaustedMsg
                  : t.schedule.creditsExhaustedMsg}
              </p>
              {creditStatus && (
                <div className="text-xs text-gray-500 space-y-1">
                  <div>{t.schedule.monthlyUsage}: ${creditStatus.monthly_cost_usd.toFixed(2)}</div>
                  <div>{t.schedule.currentBalance}: ${creditStatus.purchased_credits_usd.toFixed(2)}</div>
                </div>
              )}
              <div>
                <label className="glass-label">{t.schedule.creditAmount}</label>
                <div className="flex gap-2 mt-1">
                  {[5, 10, 25, 50].map((amt) => (
                    <button
                      key={amt}
                      onClick={() => setPurchaseAmount(amt)}
                      className={`px-3 py-2 rounded-lg text-sm font-medium border ${
                        purchaseAmount === amt
                          ? "border-purple-400 bg-purple-500/20 text-purple-300"
                          : "border-white/10 bg-white/[0.03] text-gray-400 hover:border-white/20"
                      }`}
                    >
                      ${amt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-3 px-6 py-4 border-t border-white/[0.08] bg-white/[0.03] rounded-b-2xl">
              <button
                onClick={() => setShowPurchaseModal(false)}
                className="glass-btn-secondary text-sm font-medium"
              >
                {t.common.cancel}
              </button>
              <button
                onClick={handlePurchaseCredits}
                disabled={purchaseLoading}
                className="glass-btn-primary text-sm font-medium"
              >
                {purchaseLoading ? t.schedule.redirectingToPayment : `${t.schedule.purchase} $${purchaseAmount}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
