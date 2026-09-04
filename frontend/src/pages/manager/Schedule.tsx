import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as schedulesApi from "../../api/schedules";
import * as shiftTemplatesApi from "../../api/shiftTemplates";
import * as locationsApi from "../../api/locations";
import { listEmployees } from "../../api/employees";
import * as billingApi from "../../api/billing";
import type { AiCreditStatus, AutoReloadStatus, BillingUsage, ScheduleQuota } from "../../api/billing";
import { listSpecialHours } from "../../api/specialHours";
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import StatusBadge from "../../components/shared/StatusBadge";
import ScheduleGrid, { fmtHM, getDayLabel } from "../../components/shared/ScheduleGrid";
import DemoGuard from "../../components/shared/DemoGuard";
import PlanBanner from "../../components/shared/PlanBanner";
import VerifyEmailBanner from "../../components/shared/VerifyEmailBanner";
import RosterThinBanner from "../../components/shared/RosterThinBanner";
import { ScheduleLockedError, useScheduleStream } from "../../hooks/useScheduleStream";
import { usePlan } from "../../hooks/usePlan";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, spinner as spinnerClass } from "../../theme";
import type {
  Employee,
  Location,
  LocationResult,
  ShiftAssignment,
  ShiftTemplate,
  SpecialHoursDay,
} from "../../types";
import { extractTime, extractOffset } from "../../utils/shiftTime";


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
  const [startTime, setStartTime] = useState(extractTime(shift.start_time));
  const [endTime, setEndTime] = useState(extractTime(shift.end_time));

  const selectedEmployee = employees.find((e) => e.id === employeeId);

  const handleSave = () => {
    // Rebuild start/end using the shift date
    const datePrefix = shift.date;
    onSave({
      ...shift,
      employee_id: employeeId,
      employee_name: selectedEmployee?.full_name ?? shift.employee_name,
      role_name: roleName,
      // Reattach the offset the shift arrived with: dropping it silently
      // re-anchors the shift to a different instant (issue #92).
      start_time: `${datePrefix}T${startTime}:00${extractOffset(shift.start_time)}`,
      end_time: `${datePrefix}T${endTime}:00${extractOffset(shift.end_time)}`,
    });
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h3 className={`text-lg font-semibold ${text.heading}`}>{t.schedule.editShift}</h3>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-600 text-xl leading-none"
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

          <div className={`text-sm ${text.muted}`}>
            {t.common.date}: {getDayLabel(shift.date)} ({shift.date})
          </div>
        </div>

        <div className={`flex justify-between items-center px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
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

function LockedToast({
  lockedBy,
  expiresAt,
  onExpired,
}: {
  lockedBy: string;
  expiresAt: Date;
  onExpired: () => void;
}) {
  const { t } = useLanguage();
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000)),
  );
  useEffect(() => {
    const id = setInterval(() => {
      const next = Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000));
      setRemaining(next);
      if (next === 0) onExpired();
    }, 1000);
    return () => clearInterval(id);
  }, [expiresAt, onExpired]);
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const body = t.schedule.lockedToastBody
    .replace("{locked_by}", lockedBy)
    .replace("{countdown}", `${mm}:${ss}`);
  return (
    <div className="fixed bottom-4 right-4 max-w-sm rounded-lg bg-amber-100 border border-amber-400 text-amber-900 p-4 shadow-lg z-50">
      <div className="font-semibold">{t.schedule.lockedToastTitle}</div>
      <div className="text-sm">{body}</div>
    </div>
  );
}

export default function Schedule() {
  const { t } = useLanguage();
  const [startDate, setStartDate] = useState(getNextMonday());
  const [endDate, setEndDate] = useState(addDays(getNextMonday(), 6));
  const weekStart = startDate; // alias for compatibility
  const { results, isStreaming, error, lockedError, generate, reset } =
    useScheduleStream();
  const [actionError, setActionError] = useState("");

  // Free-tier plan state — drives the PlanBanner and gates AI generation.
  // `plan` is null while loading or if the fetch failed; every gate below
  // must fail OPEN on null so a plan-fetch outage never blocks scheduling.
  const { plan, refresh: refreshPlan } = usePlan();

  // Free-plan monthly generation cap. FAIL OPEN: `plan` is null while
  // loading or on fetch failure, so the optional-chain short-circuits to
  // `undefined` (falsy) and nothing is disabled — the server remains the
  // real enforcement point.
  const generationCapReached =
    plan?.plan === "free" &&
    plan.schedules.limit !== null &&
    plan.schedules.count >= plan.schedules.limit;

  // Handle return from Stripe upgrade checkout: confirm the session,
  // refresh plan state, then strip the query param so a page reload
  // doesn't re-trigger confirmation.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const sessionId = searchParams.get("upgrade_session_id");
    if (!sessionId) return;
    billingApi
      .confirmUpgrade(sessionId)
      .then(() => refreshPlan())
      .catch((err) => {
        console.error("Upgrade confirmation failed", err);
        setActionError(
          err instanceof Error ? err.message : t.schedule.upgradeConfirmFailed
        );
      })
      .finally(() => {
        searchParams.delete("upgrade_session_id");
        setSearchParams(searchParams, { replace: true });
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Schedule-lock toast state — populated by either the stream hook
  // (generate path) or the approve handler.
  const [lockedBy, setLockedBy] = useState<string | null>(null);
  const [lockExpiresAt, setLockExpiresAt] = useState<Date | null>(null);
  const lockActive = lockedBy !== null && lockExpiresAt !== null;

  useEffect(() => {
    if (lockedError) {
      setLockedBy(lockedError.lockedBy);
      setLockExpiresAt(lockedError.expiresAt);
    }
  }, [lockedError]);

  // Refresh plan state once a generation stream ends — success OR failure —
  // so the free-tier generation count (and generationCapReached gating) is
  // current without requiring a page reload. useScheduleStream.generate()
  // is fire-and-forget with no completion callback, so we watch for the
  // isStreaming true -> false transition instead.
  //
  // We deliberately do NOT skip this on error/lockedError: a
  // schedule_limit_reached 402 means the *server's* count is already at
  // cap while our locally-cached plan.schedules.count is stale (usually
  // because another manager on the same ownership group used the last
  // generation — see usePlan.ts's staleness note). Not refreshing would
  // leave generationCapReached false and the button enabled, so a user
  // could click straight into the same 402 again with no explanation.
  // useScheduleStream flattens every 402/409 body down to a plain message
  // string before exposing it as `error` (see its response.ok branch,
  // which extracts `detail.message` and discards `detail.code`), so we
  // can't cheaply distinguish "cap reached" from other failures here
  // without changing that hook (out of scope for this fix). Refreshing
  // unconditionally costs one extra GET /billing/plan per failed
  // generation, which is a good trade for never leaving the UI stuck.
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      void refreshPlan();
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, refreshPlan]);

  // AI credit & schedule quota state
  const [creditStatus, setCreditStatus] = useState<AiCreditStatus | null>(null);
  const [scheduleQuota, setScheduleQuota] = useState<ScheduleQuota | null>(null);
  const [showBillingModal, setShowBillingModal] = useState(false);
  const [purchaseReason, setPurchaseReason] = useState<"ai" | "schedules">("ai");
  const [autoReload, setAutoReload] = useState<AutoReloadStatus | null>(null);
  const [autoReloadEditing, setAutoReloadEditing] = useState(false);
  const [autoReloadDraft, setAutoReloadDraft] = useState<{
    enabled: boolean;
    threshold_usd: number;
    amount_usd: number;
  }>({ enabled: true, threshold_usd: 2, amount_usd: 10 });
  const [autoReloadSaving, setAutoReloadSaving] = useState(false);
  const [reactivating, setReactivating] = useState(false);
  const [billingUsage, setBillingUsage] = useState<BillingUsage | null>(null);
  const [approvedLocations, setApprovedLocations] = useState<Set<string>>(
    new Set()
  );
  const [rejectedLocations, setRejectedLocations] = useState<Set<string>>(
    new Set()
  );

  // Special hours for the current week (used to render a badge on
  // matching day-of-week column headers inside each location card).
  const [specialHours, setSpecialHours] = useState<SpecialHoursDay[]>([]);
  useEffect(() => {
    if (!weekStart) return;
    const from_date = weekStart;
    // The schedule range is capped at 7 days (Mon..Sun in the default
    // case, but a manager may pick a shorter range). Use endDate when
    // available so we cover exactly the visible week.
    const to_date = endDate || addDays(weekStart, 6);
    listSpecialHours({ from_date, to_date })
      .then(setSpecialHours)
      .catch(() => setSpecialHours([]));
  }, [weekStart, endDate]);

  // Editable shifts: keyed by location_id
  const [editedShifts, setEditedShifts] = useState<
    Record<string, ShiftAssignment[]>
  >({});
  const [editingShift, setEditingShift] = useState<{
    locationId: string;
    shiftIndex: number;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  // Per-location sequence counter (#99 review): guards against an
  // in-flight draft save resolving after a newer one and clobbering it.
  const saveSeq = useRef<Record<string, number>>({});

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

  // Once plan state loads, if AI generation is gated (free plan — see
  // usePlan's fail-open contract: `plan` stays null on fetch failure, so
  // this never fires when we don't actually know), fall back to local so
  // the page stays usable instead of leaving the AI mode selected but
  // unreachable.
  useEffect(() => {
    if (plan && !plan.can_generate_ai && generateMode === "ai") {
      setGenerateMode("local");
    }
  }, [plan]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Load auto-reload settings on mount
  useEffect(() => {
    billingApi.getAutoReload().then((s) => {
      setAutoReload(s);
      setAutoReloadDraft({
        enabled: s.enabled,
        threshold_usd: s.threshold_usd,
        amount_usd: s.amount_usd,
      });
    }).catch(() => {});
  }, []);

  // Load billing usage (for the Pending Monthly Charges panel)
  useEffect(() => {
    billingApi.getUsage().then(setBillingUsage).catch(() => {});
  }, []);

  const handleSaveAutoReload = async () => {
    setAutoReloadSaving(true);
    try {
      const updated = await billingApi.updateAutoReload(autoReloadDraft);
      setAutoReload(updated);
      setAutoReloadEditing(false);
      fetchCredits();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to save auto-reload settings");
    } finally {
      setAutoReloadSaving(false);
    }
  };

  const handleRetryAutoReload = async () => {
    try {
      const updated = await billingApi.retryAutoReload();
      setAutoReload(updated);
      fetchCredits();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : t.schedule.retryFailed);
    }
  };

  const handleOpenPortal = async () => {
    try {
      const { url } = await billingApi.getPortalLink();
      window.location.href = url;
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Could not open billing portal");
    }
  };

  const handleReactivate = async () => {
    setReactivating(true);
    try {
      const { url } = await billingApi.reactivateCheckout();
      window.location.href = url;
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Could not start reactivation");
      setReactivating(false);
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
      setShowBillingModal(true);
      return;
    }
    // For AI mode, also check AI credits
    if (mode === "ai" && creditStatus && !creditStatus.can_generate) {
      setPurchaseReason("ai");
      setShowBillingModal(true);
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
        if (err instanceof ScheduleLockedError) {
          setLockedBy(err.lockedBy);
          setLockExpiresAt(err.expiresAt);
          return;
        }
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

  // Save a draft's shift list and, if it's still the latest save for this
  // location, apply the server's re-annotated response (or its error).
  // Two edits to the same location can be in flight at once; only the
  // response for the latest save may update state, else a slower earlier
  // request could resolve last and clobber a newer edit — or, for a
  // delete, resurrect a shift that was just removed.
  const persistDraft = async (
    locationId: string,
    scheduleId: string,
    next: ShiftAssignment[]
  ) => {
    const seq = (saveSeq.current[locationId] ?? 0) + 1;
    saveSeq.current[locationId] = seq;
    try {
      const saved = await schedulesApi.updateShifts(scheduleId, next);
      if (saveSeq.current[locationId] === seq) {
        setEditedShifts((prev) => ({ ...prev, [locationId]: saved.shifts }));
      }
    } catch (err: unknown) {
      if (saveSeq.current[locationId] === seq) {
        setActionError(err instanceof Error ? err.message : "Save failed");
      }
    }
  };

  const handleSaveShift = async (updated: ShiftAssignment) => {
    if (!editingShift) return;
    const { locationId, shiftIndex } = editingShift;
    const result = results.find((r) => r.location_id === locationId);
    const current = editedShifts[locationId] ?? result?.shifts ?? [];
    const next = [...current];
    next[shiftIndex] = updated;
    setEditedShifts((prev) => ({ ...prev, [locationId]: next }));
    setEditingShift(null);
    // Save now rather than at approve (#99): the server re-annotates the
    // list, so the asterisk on a hand-edited shift is right immediately.
    if (result?.schedule_id) {
      await persistDraft(locationId, result.schedule_id, next);
    }
  };

  const handleDeleteShift = async () => {
    if (!editingShift) return;
    const { locationId, shiftIndex } = editingShift;
    const result = results.find((r) => r.location_id === locationId);
    const current = editedShifts[locationId] ?? result?.shifts ?? [];
    const next = [...current];
    next.splice(shiftIndex, 1);
    setEditedShifts((prev) => ({ ...prev, [locationId]: next }));
    setEditingShift(null);
    // Save now, same as an edit (#99): a delete changes cap counts, so the
    // remaining shifts' asterisks must be recomputed, and routing through
    // the same guarded save prevents a stale in-flight response from
    // resurrecting the deleted shift.
    if (result?.schedule_id) {
      await persistDraft(locationId, result.schedule_id, next);
    }
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
      <h1 className={`text-2xl font-bold ${text.heading} mb-6`}>{t.schedule.title}</h1>

      {/* Cancellation banner — read-only grace period */}
      {billingUsage?.is_read_only && (
        <div className="bg-red-50 border border-red-300 rounded-lg p-6 mb-6">
          <h3 className="text-lg font-semibold text-red-900 mb-2">
            {t.schedule.cancellationCardTitle}
          </h3>
          <p className="text-sm text-red-800 mb-1">
            {t.schedule.cancellationEndedOn.replace(
              "{date}",
              billingUsage.canceled_at ? new Date(billingUsage.canceled_at).toLocaleDateString() : ""
            )}
          </p>
          <p className="text-sm text-red-800 mb-4">
            {t.schedule.cancellationDeletionOn.replace(
              "{date}",
              billingUsage.scheduled_deletion_at
                ? new Date(billingUsage.scheduled_deletion_at).toLocaleDateString()
                : ""
            )}
          </p>
          <button
            onClick={handleReactivate}
            disabled={reactivating}
            className="px-4 py-2 bg-red-600 text-white rounded font-medium hover:bg-red-700 disabled:opacity-50"
          >
            {reactivating ? t.schedule.redirectingToPayment : t.schedule.reactivateSubscription}
          </button>
        </div>
      )}

      {/* Auto-reload failed banner */}
      {autoReload?.failed_at && (
        <div className="bg-red-50 border border-red-300 rounded-lg p-4 mb-6 flex items-center justify-between gap-4">
          <div>
            <div className="font-semibold text-red-900">{t.schedule.billingOnHoldTitle}</div>
            <div className="text-sm text-red-800">
              {t.schedule.billingOnHoldBody.replace(
                "{date}",
                new Date(autoReload.failed_at).toLocaleString()
              )}
            </div>
          </div>
          <div className="flex gap-2 flex-shrink-0">
            <button
              onClick={handleRetryAutoReload}
              className="px-3 py-1 bg-red-600 text-white rounded text-sm font-medium"
            >
              {t.schedule.retryPayment}
            </button>
            <button
              onClick={handleOpenPortal}
              className="px-3 py-1 bg-white border border-red-300 text-red-800 rounded text-sm font-medium"
            >
              {t.schedule.updateCard}
            </button>
          </div>
        </div>
      )}

      {/* Pending Monthly Charges */}
      {billingUsage && (
        <div className="mb-4 p-4 rounded-lg border border-sage/20 bg-sage/[0.05]">
          <h3 className={`text-sm font-semibold mb-2 ${text.heading}`}>
            {t.schedule.pendingChargesTitle}
          </h3>
          {billingUsage.pending_invoice_items.length === 0 ? (
            <p className={`text-xs ${text.muted}`}>{t.schedule.pendingChargesEmpty}</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {billingUsage.pending_invoice_items.map((it) => (
                <li key={`${it.kind}-${it.period}`} className="flex justify-between">
                  <span>
                    {it.kind === "invoice_item_storage"
                      ? t.schedule.pendingChargeStorage
                      : t.schedule.pendingChargeEmployees}{" "}
                    <span className={text.muted}>({it.period})</span>
                  </span>
                  <span className="font-medium">${it.amount_usd.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Credit & Quota Status Banner */}
      {(creditStatus || scheduleQuota) && (
        <div className="mb-4 space-y-2">
          {/* Schedule quota */}
          {scheduleQuota && (
            <div className={`p-3 rounded-lg border text-sm flex items-center justify-between ${
              scheduleQuota.can_generate
                ? `${border.default} ${bg.sectionSubtle} ${text.muted}`
                : "border-red-200 bg-red-50 text-red-700"
            }`}>
              <div className="flex items-center gap-4">
                <span>
                  {t.schedule.schedulesUsed}: {scheduleQuota.schedules_used} / {scheduleQuota.schedules_included}
                </span>
                {scheduleQuota.is_over_included && (
                  <span className={`text-xs ${text.muted}`}>
                    ({t.schedule.scheduleFreeTierUsed})
                  </span>
                )}
              </div>
              {/* Credits are a paid-plan overage mechanism: check_can_generate
                  stops a free group on the plan cap whatever their balance, so
                  buying them could not unblock a free tenant. Shown disabled
                  rather than removed, with the reason, so the path stays
                  visible; the plan banner offers Upgrade. */}
              {scheduleQuota.is_over_included && !scheduleQuota.can_generate && (
                <div className="flex items-center gap-2">
                  {scheduleQuota.plan === "free" && (
                    <span id="buy-credits-reason" className={`text-xs ${text.muted}`}>
                      {t.schedule.buyCreditsPaidOnly}
                    </span>
                  )}
                  <button
                    onClick={() => { setPurchaseReason("schedules"); setShowBillingModal(true); }}
                    disabled={scheduleQuota.plan === "free"}
                    aria-describedby={
                      scheduleQuota.plan === "free" ? "buy-credits-reason" : undefined
                    }
                    className="glass-btn-primary text-xs px-3 py-1 whitespace-nowrap disabled:cursor-not-allowed"
                  >
                    {t.schedule.buyCredits}
                  </button>
                </div>
              )}
            </div>
          )}
          {/* AI credits */}
          {creditStatus && (
            <div className={`p-3 rounded-lg border text-sm flex items-center justify-between ${
              creditStatus.can_generate
                ? `${border.default} ${bg.sectionSubtle} ${text.muted}`
                : "border-red-200 bg-red-50 text-red-700"
            }`}>
              <div className="flex items-center gap-4">
                <span>
                  {t.schedule.aiCredits}:
                  {creditStatus.is_over_included
                    ? ` $${creditStatus.purchased_credits_usd.toFixed(2)} ${t.schedule.purchasedRemaining}`
                    : ` $${creditStatus.included_remaining_usd.toFixed(2)} ${t.schedule.freeRemaining}`
                  }
                </span>
                {creditStatus.is_over_included && (
                  <span className={`text-xs ${text.muted}`}>
                    ({t.schedule.freeTierUsed})
                  </span>
                )}
              </div>
              {creditStatus.is_over_included && !creditStatus.can_generate && (
                <button
                  onClick={() => { setPurchaseReason("ai"); setShowBillingModal(true); }}
                  className="glass-btn-primary text-xs px-3 py-1"
                >
                  {t.schedule.buyCredits}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Free-tier status — renders nothing on a paid plan or while plan
          state is unknown (null), per usePlan's fail-open contract. */}
      {/* Above PlanBanner: an unverified address blocks generation
          outright, so it is the first thing to fix. */}
      <VerifyEmailBanner />
      {plan && <PlanBanner plan={plan} />}

      <div className="flex items-center gap-4 mb-6 flex-wrap">
        <div className="flex items-center gap-3">
          <label className={`text-sm font-medium ${text.secondary}`}>{t.schedule.startLabel}</label>
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
          <label className={`text-sm font-medium ${text.secondary}`}>{t.schedule.endLabel}</label>
          <input
            type="date"
            value={endDate}
            min={startDate}
            max={addDays(startDate, 6)}
            onChange={(e) => setEndDate(e.target.value)}
            className="glass-input"
          />
          <span className={`text-xs ${text.muted}`}>
            ({daysBetween(startDate, endDate)} {daysBetween(startDate, endDate) !== 1 ? t.common.days : t.common.day})
          </span>
        </div>
        <button
          onClick={() => handleGenerateClick("local")}
          disabled={isStreaming || lockActive || plan?.over_limit === true || generationCapReached}
          title={
            generationCapReached
              ? t.schedule.generationCapReachedNotice
                  .replace("{used}", String(plan?.schedules.count))
                  .replace("{max}", String(plan?.schedules.limit))
              : undefined
          }
          className="glass-btn-success px-5 py-3 rounded-lg font-semibold text-sm"
        >
          {isStreaming && generateMode === "local" ? t.schedule.generating : t.schedule.localGenerate}
        </button>
        <DemoGuard>
          <button
            onClick={() => handleGenerateClick("ai")}
            disabled={isStreaming || lockActive || plan?.can_generate_ai === false || generationCapReached}
            title={
              plan?.can_generate_ai === false
                ? t.planBanner.reasonAiRequiresPaid
                : generationCapReached
                  ? t.schedule.generationCapReachedNotice
                      .replace("{used}", String(plan?.schedules.count))
                      .replace("{max}", String(plan?.schedules.limit))
                  : undefined
            }
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

      {/* Free-plan generation cap notice — visible (not just a hover
          title) per the "disable, don't hide" rule, and only rendered
          when we positively know the cap is reached (plan !== null). */}
      {generationCapReached && (
        <p className={`-mt-4 mb-6 text-sm ${text.muted}`}>
          {t.schedule.generationCapReachedNotice
            .replace("{used}", String(plan?.schedules.count))
            .replace("{max}", String(plan?.schedules.limit))}
        </p>
      )}

      {/* Template Picker Modal */}
      {showTemplatePicker && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-lg mx-4 max-h-[80vh] flex flex-col">
            <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
              <h2 className={`text-lg font-semibold ${text.heading}`}>
                {t.schedule.selectTemplates}
              </h2>
              <button
                onClick={() => setShowTemplatePicker(false)}
                className="text-gray-500 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              <p className={`text-sm ${text.muted} mb-4`}>
                {t.schedule.selectTemplatesDesc}
              </p>

              {loadingTemplates && (
                <div className="flex items-center gap-3 py-4">
                  <div className={`h-5 w-5 animate-spin rounded-full border-2 ${spinnerClass}`} />
                  <span className={`text-sm ${text.muted}`}>
                    {t.schedule.loadingTemplates}
                  </span>
                </div>
              )}

              {!loadingTemplates && templates.length === 0 && (
                <p className={`text-sm ${text.muted}`}>
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
                        className={`mb-4 border ${border.default} rounded-lg`}
                      >
                        <div className={`px-4 py-2 ${bg.tableHeader} rounded-t-lg flex items-center gap-2`}>
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = someSelected;
                            }}
                            onChange={() => toggleAllForLocation(locId)}
                            className="rounded border-sage/30 bg-white/50"
                          />
                          <span className={`text-sm font-semibold ${text.secondary}`}>
                            {locationName(locId)}
                          </span>
                          <span className={`text-xs ${text.muted}`}>
                            ({locTemplates.length} {locTemplates.length !== 1 ? t.schedule.templates : t.schedule.template})
                          </span>
                        </div>
                        <div className="px-4 py-2 space-y-1">
                          {locTemplates.map((tmpl) => (
                            <label
                              key={tmpl.id}
                              className={`flex items-center gap-2 text-sm ${text.secondary} cursor-pointer py-1`}
                            >
                              <input
                                type="checkbox"
                                checked={selectedTemplateIds.has(tmpl.id)}
                                onChange={() => toggleTemplate(tmpl.id)}
                                className="rounded border-sage/30 bg-white/50"
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
              <div className={`px-6 py-3 bg-emerald-50 border-t ${border.default}`}>
                <label className={`block text-sm font-medium ${text.secondary} mb-2`}>
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
                      <span className={`text-sm font-medium ${text.secondary}`}>{t.schedule.rotation}</span>
                      <p className={`text-xs ${text.muted}`}>{t.schedule.rotationDesc}</p>
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
                      <span className={`text-sm font-medium ${text.secondary}`}>{t.schedule.random}</span>
                      <p className={`text-xs ${text.muted}`}>{t.schedule.randomDesc}</p>
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
                      <span className={`text-sm font-medium ${text.secondary}`}>{t.schedule.rotationHistory}</span>
                      <p className={`text-xs ${text.muted}`}>{t.schedule.rotationHistoryDesc}</p>
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
                      <span className={`text-sm font-medium ${text.secondary}`}>{t.schedule.maxHours}</span>
                      <p className={`text-xs ${text.muted}`}>{t.schedule.maxHoursDesc}</p>
                    </div>
                  </label>
                </div>
                {localStrategy === "rotation_history" && (
                  <div className={`mt-3 pt-3 border-t ${border.default}`}>
                    <div className="flex items-center justify-between mb-2">
                      <label className={`text-sm font-medium ${text.secondary}`}>{t.schedule.fairnessWeight}</label>
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
                    <div className={`flex justify-between text-xs ${text.muted} mt-1`}>
                      <span>{t.schedule.moreRandom}</span>
                      <span>{t.schedule.moreFair}</span>
                    </div>
                  </div>
                )}
                {localStrategy === "max_hours" && (
                  <div className={`mt-3 pt-3 border-t ${border.default} space-y-4`}>
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <label className={`text-sm font-medium ${text.secondary}`}>{t.schedule.maxHoursLimit}</label>
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
                      <div className={`flex justify-between text-xs ${text.muted} mt-1`}>
                        <span>4h</span>
                        <span>60h</span>
                      </div>
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <label className={`text-sm font-medium ${text.secondary}`}>{t.schedule.hourStrictness}</label>
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
                      <div className={`flex justify-between text-xs ${text.muted} mt-1`}>
                        <span>{t.schedule.noEnforcement}</span>
                        <span>{t.schedule.strictEnforcement}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className={`flex justify-between items-center px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
              <span className={`text-xs ${text.muted}`}>
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
        <div className={`${text.muted} text-sm`}>
          {t.schedule.waitingResults}
        </div>
      )}

      {allComplete && (
        <div className="mb-6 p-4 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-xl text-center font-semibold">
          {t.schedule.allComplete}
        </div>
      )}

      {specialHours.length > 0 && (
        <div className={`mb-6 glass-card p-4`}>
          <div className="flex items-center justify-between mb-2">
            <h3 className={`text-sm font-semibold ${text.heading}`}>
              ★ {t.specialHours.schedulePreviewTitle}
            </h3>
          </div>
          <p className={`text-xs ${text.muted} mb-3`}>
            {t.specialHours.schedulePreviewHelp}
          </p>
          <div className="space-y-1">
            {Object.entries(
              specialHours.reduce<Record<string, SpecialHoursDay[]>>((acc, sh) => {
                (acc[sh.location_id] ??= []).push(sh);
                return acc;
              }, {}),
            )
              .map(([loc_id, rows]) => {
                const loc = locations.find((l) => l.id === loc_id);
                const name = loc?.name ?? loc_id.slice(0, 8);
                rows.sort((a, b) => a.date.localeCompare(b.date));
                return (
                  <div key={loc_id} className="flex flex-wrap items-center gap-2">
                    <span className={`text-xs font-medium ${text.body} min-w-[140px]`}>
                      {name}
                    </span>
                    {rows.map((sh) => (
                      <span
                        key={sh.id}
                        className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-amber-100 text-amber-900"
                        title={`${sh.label ?? t.specialHours.scheduleBadge} · ${fmtHM(sh.open_time)}–${fmtHM(sh.close_time)} on ${sh.date}`}
                      >
                        ★ {sh.label ?? t.specialHours.scheduleBadge} ·{" "}
                        {fmtHM(sh.open_time)}–{fmtHM(sh.close_time)} ·{" "}
                        {sh.date}
                      </span>
                    ))}
                  </div>
                );
              })}
          </div>
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
          const specialHoursByDate: Record<string, SpecialHoursDay> = {};
          for (const sh of specialHours) {
            if (sh.location_id === locationResult.location_id) {
              specialHoursByDate[sh.date] = sh;
            }
          }
          const locationSpecialHours = Object.values(specialHoursByDate).sort(
            (a, b) => a.date.localeCompare(b.date),
          );

          return (
            <div
              key={locationResult.location_id}
              className={`glass-card ${
                printLocationId === locationResult.location_id ? "print-target" : ""
              }`}
              data-print={printLocationId === locationResult.location_id ? "true" : undefined}
            >
              <div className={`p-4 border-b ${border.default} flex items-center justify-between`}>
                <div className="flex items-center gap-3">
                  <h3 className={`text-lg font-semibold ${text.heading}`}>
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
                    <span className={`text-xs ${text.muted} me-2`}>
                      {t.schedule.clickToEdit}
                    </span>
                  )}
                  {!decided && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleApprove(locationResult)}
                        disabled={saving || lockActive}
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

              {locationSpecialHours.length > 0 && (
                <div className={`px-4 py-2 border-b ${border.subtle} flex flex-wrap items-center gap-2`}>
                  {locationSpecialHours.map((sh) => (
                    <span
                      key={sh.id}
                      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-900"
                    >
                      ★ {sh.label ?? t.specialHours.scheduleBadge} · {fmtHM(sh.open_time)}–{fmtHM(sh.close_time)} · {sh.date}
                    </span>
                  ))}
                </div>
              )}

              <RosterThinBanner summary={locationResult.preference_summary} />

              {currentShifts.length > 0 && (
                <ScheduleGrid
                  shifts={currentShifts}
                  editable={!decided}
                  employees={employees}
                  onEditShift={(idx) =>
                    handleEditShift(locationResult.location_id, idx)
                  }
                  specialHoursByDate={specialHoursByDate}
                />
              )}

              {currentShifts.length === 0 &&
                locationResult.errors.length === 0 && (
                  <div className={`p-4 ${text.muted} text-sm`}>
                    {t.schedule.noShiftsGenerated}
                  </div>
                )}
            </div>
          );
        })}
      </div>

      {/* Auto-Reload Settings Modal */}
      {showBillingModal && autoReload && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-md mx-4">
            <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
              <h3 className={`text-lg font-semibold ${text.heading}`}>{t.schedule.autoReloadTitle}</h3>
              <button
                onClick={() => setShowBillingModal(false)}
                className="text-gray-500 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className={`text-sm ${text.muted}`}>
                {purchaseReason === "schedules"
                  ? t.schedule.scheduleQuotaExhaustedMsg
                  : t.schedule.creditsExhaustedMsg}
              </p>
              <p className={`text-sm ${text.muted}`}>{t.schedule.autoReloadDescription}</p>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className={`text-xs ${text.muted}`}>{t.schedule.balance}</div>
                  <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.current_balance_usd.toFixed(2)}</div>
                </div>
                <div>
                  <div className={`text-xs ${text.muted}`}>{t.schedule.threshold}</div>
                  <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.threshold_usd.toFixed(2)}</div>
                </div>
                <div>
                  <div className={`text-xs ${text.muted}`}>{t.schedule.refillAmount}</div>
                  <div className={`text-lg font-semibold ${text.heading}`}>${autoReload.amount_usd.toFixed(2)}</div>
                </div>
              </div>
              {autoReloadEditing && (
                <div className="space-y-3 pt-2 border-t border-sage/10">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={autoReloadDraft.enabled}
                      onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, enabled: e.target.checked })}
                    />
                    {t.schedule.autoReloadEnabled}
                  </label>
                  <label className="block text-sm">
                    {t.schedule.threshold}: $
                    <input
                      type="number"
                      min="0.5"
                      step="0.5"
                      value={autoReloadDraft.threshold_usd}
                      onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, threshold_usd: parseFloat(e.target.value) || 0 })}
                      className="ms-2 border rounded px-2 py-1 w-24"
                    />
                  </label>
                  <label className="block text-sm">
                    {t.schedule.refillAmount}: $
                    <input
                      type="number"
                      min="0.5"
                      step="1"
                      value={autoReloadDraft.amount_usd}
                      onChange={(e) => setAutoReloadDraft({ ...autoReloadDraft, amount_usd: parseFloat(e.target.value) || 0 })}
                      className="ms-2 border rounded px-2 py-1 w-24"
                    />
                  </label>
                </div>
              )}
            </div>
            <div className={`flex justify-end gap-3 px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
              {!autoReloadEditing ? (
                <>
                  <button
                    type="button"
                    onClick={handleOpenPortal}
                    className="glass-btn-secondary text-sm font-medium me-auto"
                  >
                    {t.schedule.manageBilling}
                  </button>
                  <button
                    onClick={() => setShowBillingModal(false)}
                    className="glass-btn-secondary text-sm font-medium"
                  >
                    {t.common.close}
                  </button>
                  <button
                    onClick={() => setAutoReloadEditing(true)}
                    className="glass-btn-primary text-sm font-medium"
                  >
                    {t.common.edit}
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => {
                      setAutoReloadEditing(false);
                      setAutoReloadDraft({
                        enabled: autoReload.enabled,
                        threshold_usd: autoReload.threshold_usd,
                        amount_usd: autoReload.amount_usd,
                      });
                    }}
                    className="glass-btn-secondary text-sm font-medium"
                  >
                    {t.common.cancel}
                  </button>
                  <button
                    onClick={handleSaveAutoReload}
                    disabled={autoReloadSaving}
                    className="glass-btn-primary text-sm font-medium"
                  >
                    {autoReloadSaving ? t.common.saving : t.common.save}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {lockActive && lockExpiresAt && (
        <LockedToast
          lockedBy={lockedBy!}
          expiresAt={lockExpiresAt}
          onExpired={() => {
            setLockedBy(null);
            setLockExpiresAt(null);
          }}
        />
      )}
    </div>
  );
}
