import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type ApprovedScheduleItem,
  type Export7ShiftsResult,
  exportTo7Shifts,
  listApprovedDates,
  listApprovedSchedules,
} from "../../api/exportSchedules";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, calendarToday } from "../../theme";
import { formatTime } from "../../utils/shiftTime";

function toDateStr(d: Date): string {
  return d.toISOString().split("T")[0];
}

function getDayLabel(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return (
    d.toLocaleDateString("en-US", { weekday: "short" }) +
    " " +
    d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
  );
}

/** Build the calendar grid for a given month (year, monthIndex 0-based). */
function buildMonthGrid(year: number, month: number): (string | null)[][] {
  const firstDay = new Date(year, month, 1);
  // Monday=0 .. Sunday=6
  const startDow = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const weeks: (string | null)[][] = [];
  let week: (string | null)[] = Array(startDow).fill(null);

  for (let day = 1; day <= daysInMonth; day++) {
    week.push(toDateStr(new Date(year, month, day)));
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }
  return weeks;
}

export default function ExportSchedules() {
  const { t } = useLanguage();
  const [selectedDate, setSelectedDate] = useState(toDateStr(new Date()));
  const [schedules, setSchedules] = useState<ApprovedScheduleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 7shifts export state
  const [token, setToken] = useState("");
  const [showTokenModal, setShowTokenModal] = useState(false);
  const [pendingExportIds, setPendingExportIds] = useState<string[]>([]);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<Export7ShiftsResult | null>(
    null
  );

  // Selection state
  const [selectedShifts, setSelectedShifts] = useState<Set<string>>(new Set());

  // Calendar dropdown state
  const [calendarOpen, setCalendarOpen] = useState(false);
  const calendarRef = useRef<HTMLDivElement>(null);
  const [viewYear, setViewYear] = useState(
    () => new Date(selectedDate + "T00:00:00").getFullYear()
  );
  const [viewMonth, setViewMonth] = useState(
    () => new Date(selectedDate + "T00:00:00").getMonth()
  );

  // Dates that have approved schedules (shown as orange dots on the calendar)
  const [approvedDates, setApprovedDates] = useState<Set<string>>(new Set());

  const loadApprovedDates = useCallback(async (anchor: string) => {
    try {
      const dates = await listApprovedDates(anchor);
      setApprovedDates(new Set(dates));
    } catch {
      // Non-critical — dots just won't show
    }
  }, []);

  // Close calendar on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        calendarRef.current &&
        !calendarRef.current.contains(e.target as Node)
      ) {
        setCalendarOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listApprovedSchedules(selectedDate);
      setSchedules(data);
      setSelectedShifts(new Set());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [selectedDate]);

  // Load approved dates whenever the visible calendar month changes
  useEffect(() => {
    const anchor = toDateStr(new Date(viewYear, viewMonth, 15));
    loadApprovedDates(anchor);
  }, [viewYear, viewMonth, loadApprovedDates]);

  const allShifts = useMemo(
    () => schedules.flatMap((s) => s.shifts),
    [schedules]
  );

  const unexportedShiftIds = useMemo(
    () => allShifts.filter((s) => !s.exported_at).map((s) => s.id),
    [allShifts]
  );

  const toggleShift = (id: string) => {
    setSelectedShifts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllForSchedule = (sched: ApprovedScheduleItem) => {
    const ids = sched.shifts.map((s) => s.id);
    const allSelected = ids.every((id) => selectedShifts.has(id));
    setSelectedShifts((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (allSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  };

  const selectAllUnexported = () => {
    setSelectedShifts(new Set(unexportedShiftIds));
  };

  const handleExportTo7Shifts = () => {
    const ids = Array.from(selectedShifts);
    if (ids.length === 0) {
      setError("Select at least one shift to export");
      return;
    }
    setPendingExportIds(ids);
    setShowTokenModal(true);
    setExportResult(null);
  };

  const handleConfirmExport = async () => {
    if (!token.trim()) return;
    setShowTokenModal(false);
    setExporting(true);
    setError(null);
    setExportResult(null);
    try {
      const result = await exportTo7Shifts(token.trim(), pendingExportIds);
      setExportResult(result);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const handleDownloadJsonl = (scheduleId: string) => {
    const authToken = localStorage.getItem("token");
    const url = `/api/v1/export/jsonl/${scheduleId}`;
    // Use fetch to add auth header, then trigger download
    fetch(url, {
      headers: { Authorization: `Bearer ${authToken}` },
    })
      .then((resp) => {
        if (!resp.ok) throw new Error("Download failed");
        const cd = resp.headers.get("content-disposition");
        const match = cd?.match(/filename="(.+?)"/);
        const filename = match?.[1] ?? `schedule_${scheduleId}.jsonl`;
        return resp.blob().then((blob) => ({ blob, filename }));
      })
      .then(({ blob, filename }) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch((err) => setError(err.message));
  };

  if (loading) {
    return (
      <div className={`flex items-center justify-center h-64 ${text.muted}`}>
        {t.common.loading}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className={`text-2xl font-bold ${text.heading}`}>
            {t.exportSchedules.title}
          </h1>
          <p className={`mt-1 text-sm ${text.muted}`}>
            {t.exportSchedules.description}
          </p>
        </div>
      </div>

      {/* Week picker with custom calendar dropdown */}
      <div className="flex items-center gap-4 mb-6">
        <label className={`text-sm font-medium ${text.muted}`}>
          {t.exportSchedules.weekStarting}
        </label>
        <div className="relative" ref={calendarRef}>
          <button
            onClick={() => {
              const d = new Date(selectedDate + "T00:00:00");
              setViewYear(d.getFullYear());
              setViewMonth(d.getMonth());
              setCalendarOpen((prev) => !prev);
            }}
            className={`glass-input-sm min-w-[160px] text-start ${bg.interactiveHover}`}
          >
            {getDayLabel(selectedDate)}
          </button>

          {calendarOpen && (
            <div className="absolute z-50 mt-1 glass-modal p-3 w-[280px]">
              {/* Month/year nav */}
              <div className="flex items-center justify-between mb-2">
                <button
                  onClick={() => {
                    if (viewMonth === 0) {
                      setViewMonth(11);
                      setViewYear((y) => y - 1);
                    } else {
                      setViewMonth((m) => m - 1);
                    }
                  }}
                  className={`p-1 ${bg.interactiveHover} rounded ${text.muted}`}
                >
                  &larr;
                </button>
                <span className="text-sm font-semibold text-gray-700">
                  {new Date(viewYear, viewMonth).toLocaleDateString("en-US", {
                    month: "long",
                    year: "numeric",
                  })}
                </span>
                <button
                  onClick={() => {
                    if (viewMonth === 11) {
                      setViewMonth(0);
                      setViewYear((y) => y + 1);
                    } else {
                      setViewMonth((m) => m + 1);
                    }
                  }}
                  className={`p-1 ${bg.interactiveHover} rounded ${text.muted}`}
                >
                  &rarr;
                </button>
              </div>

              {/* Day-of-week headers */}
              <div className="grid grid-cols-7 mb-1">
                {["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((d) => (
                  <div
                    key={d}
                    className={`text-center text-[10px] font-medium ${text.muted} py-1`}
                  >
                    {d}
                  </div>
                ))}
              </div>

              {/* Calendar grid */}
              {buildMonthGrid(viewYear, viewMonth).map((week, wi) => (
                <div key={wi} className="grid grid-cols-7">
                  {week.map((day, di) => {
                    if (!day) {
                      return <div key={`empty-${di}`} className="h-9" />;
                    }

                    const todayStr = toDateStr(new Date());
                    const isToday = day === todayStr;
                    const isSelected = day === selectedDate;
                    const hasApproved = approvedDates.has(day);

                    let cellStyle = `${bg.interactiveHover} ${text.secondary}`;
                    if (isSelected) {
                      cellStyle = bg.calendarSelected;
                    }

                    return (
                      <button
                        key={day}
                        onClick={() => {
                          setSelectedDate(day);
                          setCalendarOpen(false);
                        }}
                        className={`relative flex flex-col items-center justify-center h-9 rounded text-sm transition-colors ${cellStyle} ${isToday ? calendarToday : ""}`}
                      >
                        <span>{new Date(day + "T00:00:00").getDate()}</span>
                        {hasApproved && (
                          <span className="absolute bottom-0.5 w-1.5 h-1.5 rounded-full bg-orange-400" />
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={() => setSelectedDate(toDateStr(new Date()))}
          className="glass-btn-secondary"
        >
          {t.exportSchedules.today}
        </button>
      </div>

      {/* Action bar */}
      {schedules.length > 0 && (
        <div className="flex items-center gap-3 mb-4">
          <button
            onClick={selectAllUnexported}
            disabled={unexportedShiftIds.length === 0}
            className="glass-btn-secondary disabled:opacity-50"
          >
            {t.exportSchedules.selectAllUnexported} ({unexportedShiftIds.length})
          </button>
          <button
            onClick={handleExportTo7Shifts}
            disabled={selectedShifts.size === 0 || exporting}
            className="glass-btn-primary disabled:opacity-50"
          >
            {exporting
              ? t.exportSchedules.exporting
              : `${t.exportSchedules.exportTo7Shifts} (${selectedShifts.size})`}
          </button>
          <span className={`text-xs ${text.muted}`}>
            {selectedShifts.size} {selectedShifts.size !== 1 ? t.common.shifts : t.common.shift}{" "}
            {t.common.selected}
          </span>
        </div>
      )}

      {/* Messages */}
      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      {exportResult && (
        <div
          className={`mb-4 ${
            exportResult.failed > 0
              ? "glass-alert-warning"
              : "glass-alert-success"
          }`}
        >
          <p className="font-medium">
            {t.exportSchedules.exportedCount} {exportResult.exported} {t.exportSchedules.shiftsTo7Shifts}
            {exportResult.failed > 0 &&
              ` ${exportResult.failed} ${t.exportSchedules.failed}`}
          </p>
          {exportResult.errors.length > 0 && (
            <ul className="mt-2 list-disc list-inside text-xs">
              {exportResult.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {schedules.length === 0 && (
        <div className="text-center py-16">
          <p className={`text-lg ${text.muted}`}>{t.exportSchedules.noApproved}</p>
          <p className={`mt-2 text-sm ${text.muted}`}>
            {t.exportSchedules.noApprovedSub}
          </p>
        </div>
      )}

      {/* Schedules by location */}
      <div className="space-y-6">
        {schedules.map((sched) => {
          const allSelected = sched.shifts.every((s) =>
            selectedShifts.has(s.id)
          );
          const someSelected =
            !allSelected &&
            sched.shifts.some((s) => selectedShifts.has(s.id));

          return (
            <div
              key={sched.schedule_id}
              className="glass-card"
            >
              <div className={`p-4 border-b ${border.default} flex items-center justify-between`}>
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected;
                    }}
                    onChange={() => toggleAllForSchedule(sched)}
                    className="rounded border-sage/30 bg-white/50"
                  />
                  <h3 className={`text-lg font-semibold ${text.heading}`}>
                    {sched.location_name}
                  </h3>
                  <span className={`text-xs ${text.muted}`}>
                    {t.exportSchedules.weekOf} {sched.week_start_date}
                  </span>
                  <span
                    className={`text-xs px-2 py-0.5 rounded font-medium ${
                      sched.exported_shifts === sched.total_shifts
                        ? "bg-emerald-100 text-emerald-700"
                        : sched.exported_shifts > 0
                          ? "bg-yellow-100 text-yellow-700"
                          : "bg-sage/10 text-gray-500"
                    }`}
                  >
                    {sched.exported_shifts}/{sched.total_shifts} {t.exportSchedules.exported}
                  </span>
                </div>
                <button
                  onClick={() => handleDownloadJsonl(sched.schedule_id)}
                  className="glass-btn-secondary px-3 py-1 text-xs"
                >
                  {t.exportSchedules.downloadJsonl}
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-full">
                  <thead className={bg.tableHeader}>
                    <tr>
                      <th className="px-4 py-2 w-8"></th>
                      <th className={`px-4 py-2 text-start text-xs font-medium ${text.muted} uppercase`}>
                        {t.common.employee}
                      </th>
                      <th className={`px-4 py-2 text-start text-xs font-medium ${text.muted} uppercase`}>
                        {t.common.role}
                      </th>
                      <th className={`px-4 py-2 text-start text-xs font-medium ${text.muted} uppercase`}>
                        {t.common.date}
                      </th>
                      <th className={`px-4 py-2 text-start text-xs font-medium ${text.muted} uppercase`}>
                        {t.exportSchedules.time}
                      </th>
                      <th className={`px-4 py-2 text-start text-xs font-medium ${text.muted} uppercase`}>
                        {t.common.status}
                      </th>
                    </tr>
                  </thead>
                  <tbody className={`divide-y ${border.divider}`}>
                    {sched.shifts.map((shift) => (
                      <tr
                        key={shift.id}
                        className={
                          shift.exported_at
                            ? "bg-emerald-50"
                            : bg.rowHover
                        }
                      >
                        <td className="px-4 py-2">
                          <input
                            type="checkbox"
                            checked={selectedShifts.has(shift.id)}
                            onChange={() => toggleShift(shift.id)}
                            className="rounded border-sage/30 bg-white/50"
                          />
                        </td>
                        <td className={`px-4 py-2 text-sm font-medium ${text.primary}`}>
                          {shift.employee_name}
                        </td>
                        <td className={`px-4 py-2 text-sm ${text.secondary}`}>
                          {shift.role_name}
                        </td>
                        <td className={`px-4 py-2 text-sm ${text.secondary}`}>
                          {getDayLabel(shift.date)}
                        </td>
                        <td className={`px-4 py-2 text-sm ${text.secondary}`}>
                          {formatTime(shift.start_time)} &ndash;{" "}
                          {formatTime(shift.end_time)}
                        </td>
                        <td className="px-4 py-2 text-sm">
                          {shift.exported_at ? (
                            <span className="inline-flex items-center gap-1 text-emerald-600 text-xs font-medium">
                              <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
                              {t.exportSchedules.exportedLabel}{" "}
                              {new Date(shift.exported_at).toLocaleDateString()}
                            </span>
                          ) : (
                            <span className="text-gray-500 text-xs">
                              {t.exportSchedules.notExported}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>

      {/* 7Shifts Token Modal */}
      {showTokenModal && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-md mx-4">
            <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
              <h3 className={`text-lg font-semibold ${text.heading}`}>
                {t.exportSchedules.apiTokenTitle}
              </h3>
              <button
                onClick={() => setShowTokenModal(false)}
                className="text-gray-500 hover:text-gray-700 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className={`text-sm ${text.muted}`}>
                {t.exportSchedules.apiTokenDesc}{" "}
                {pendingExportIds.length} {pendingExportIds.length !== 1 ? t.common.shifts : t.common.shift}.
              </p>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={t.exportSchedules.tokenPlaceholder}
                className="glass-input w-full"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleConfirmExport();
                }}
              />
            </div>
            <div className={`flex justify-end gap-3 px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
              <button
                onClick={() => setShowTokenModal(false)}
                className="glass-btn-secondary"
              >
                {t.common.cancel}
              </button>
              <button
                onClick={handleConfirmExport}
                disabled={!token.trim()}
                className="glass-btn-primary disabled:opacity-50"
              >
                {t.exportSchedules.exportTo7Shifts}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
