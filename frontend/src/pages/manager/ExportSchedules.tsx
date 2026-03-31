import { useEffect, useMemo, useState } from "react";
import {
  type ApprovedScheduleItem,
  type Export7ShiftsResult,
  exportTo7Shifts,
  listApprovedSchedules,
} from "../../api/exportSchedules";

function getCurrentMonday(): string {
  const today = new Date();
  const day = today.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  const monday = new Date(today);
  monday.setDate(today.getDate() + diff);
  return monday.toISOString().split("T")[0];
}

function formatTime(t: string): string {
  const d = new Date(t);
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function getDayLabel(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return (
    d.toLocaleDateString("en-US", { weekday: "short" }) +
    " " +
    d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
  );
}

export default function ExportSchedules() {
  const [weekStart, setWeekStart] = useState(getCurrentMonday());
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

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listApprovedSchedules(weekStart);
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
  }, [weekStart]);

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
      <div className="flex items-center justify-center h-64 text-gray-500">
        Loading...
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            Export Approved Schedules
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            View approved schedules for the current week. Export to 7Shifts or
            download as JSONL. We attempt to export the user ID with the role ID
            it is associated with in the external system (in the case where the
            role is associated with a few roles).
          </p>
        </div>
      </div>

      {/* Week picker */}
      <div className="flex items-center gap-4 mb-6">
        <label className="text-sm font-medium text-gray-700">
          Week starting:
        </label>
        <input
          type="date"
          value={weekStart}
          onChange={(e) => setWeekStart(e.target.value)}
          className="border rounded px-3 py-1.5 text-sm"
        />
        <button
          onClick={() => setWeekStart(getCurrentMonday())}
          className="px-3 py-1.5 bg-gray-200 text-gray-700 text-sm rounded hover:bg-gray-300"
        >
          This Week
        </button>
      </div>

      {/* Action bar */}
      {schedules.length > 0 && (
        <div className="flex items-center gap-3 mb-4">
          <button
            onClick={selectAllUnexported}
            disabled={unexportedShiftIds.length === 0}
            className="px-3 py-1.5 bg-gray-100 text-gray-700 text-sm rounded hover:bg-gray-200 disabled:opacity-50"
          >
            Select All Unexported ({unexportedShiftIds.length})
          </button>
          <button
            onClick={handleExportTo7Shifts}
            disabled={selectedShifts.size === 0 || exporting}
            className="px-4 py-1.5 bg-indigo-600 text-white text-sm font-medium rounded hover:bg-indigo-700 disabled:opacity-50"
          >
            {exporting
              ? "Exporting..."
              : `Export to 7Shifts (${selectedShifts.size})`}
          </button>
          <span className="text-xs text-gray-400">
            {selectedShifts.size} shift{selectedShifts.size !== 1 ? "s" : ""}{" "}
            selected
          </span>
        </div>
      )}

      {/* Messages */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      {exportResult && (
        <div
          className={`mb-4 p-3 rounded text-sm ${
            exportResult.failed > 0
              ? "bg-yellow-50 text-yellow-800"
              : "bg-green-50 text-green-800"
          }`}
        >
          <p className="font-medium">
            Exported {exportResult.exported} shift
            {exportResult.exported !== 1 ? "s" : ""} to 7Shifts.
            {exportResult.failed > 0 &&
              ` ${exportResult.failed} failed.`}
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
        <div className="text-center py-16 text-gray-500">
          <p className="text-lg">No approved schedules for this week.</p>
          <p className="mt-2 text-sm">
            Generate and approve schedules on the Schedule page first.
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
              className="bg-white rounded-lg shadow"
            >
              <div className="p-4 border-b border-gray-200 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected;
                    }}
                    onChange={() => toggleAllForSchedule(sched)}
                    className="rounded border-gray-300"
                  />
                  <h3 className="text-lg font-semibold">
                    {sched.location_name}
                  </h3>
                  <span className="text-xs text-gray-400">
                    Week of {sched.week_start_date}
                  </span>
                  <span
                    className={`text-xs px-2 py-0.5 rounded font-medium ${
                      sched.exported_shifts === sched.total_shifts
                        ? "bg-green-100 text-green-800"
                        : sched.exported_shifts > 0
                          ? "bg-yellow-100 text-yellow-800"
                          : "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {sched.exported_shifts}/{sched.total_shifts} exported
                  </span>
                </div>
                <button
                  onClick={() => handleDownloadJsonl(sched.schedule_id)}
                  className="px-3 py-1 bg-gray-100 text-gray-700 text-xs rounded hover:bg-gray-200 font-medium"
                >
                  Download JSONL
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 w-8"></th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Employee
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Role
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Date
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Time
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {sched.shifts.map((shift) => (
                      <tr
                        key={shift.id}
                        className={
                          shift.exported_at
                            ? "bg-green-50/50"
                            : "hover:bg-gray-50"
                        }
                      >
                        <td className="px-4 py-2">
                          <input
                            type="checkbox"
                            checked={selectedShifts.has(shift.id)}
                            onChange={() => toggleShift(shift.id)}
                            className="rounded border-gray-300"
                          />
                        </td>
                        <td className="px-4 py-2 text-sm font-medium text-gray-900">
                          {shift.employee_name}
                        </td>
                        <td className="px-4 py-2 text-sm text-gray-700">
                          {shift.role_name}
                        </td>
                        <td className="px-4 py-2 text-sm text-gray-700">
                          {getDayLabel(shift.date)}
                        </td>
                        <td className="px-4 py-2 text-sm text-gray-700">
                          {formatTime(shift.start_time)} &ndash;{" "}
                          {formatTime(shift.end_time)}
                        </td>
                        <td className="px-4 py-2 text-sm">
                          {shift.exported_at ? (
                            <span className="inline-flex items-center gap-1 text-green-700 text-xs font-medium">
                              <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
                              Exported{" "}
                              {new Date(shift.exported_at).toLocaleDateString()}
                            </span>
                          ) : (
                            <span className="text-gray-400 text-xs">
                              Not exported
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
            <div className="flex items-center justify-between px-6 py-4 border-b">
              <h3 className="text-lg font-semibold">
                7Shifts API Token
              </h3>
              <button
                onClick={() => setShowTokenModal(false)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className="text-sm text-gray-600">
                Enter your 7Shifts API access token to export{" "}
                {pendingExportIds.length} shift
                {pendingExportIds.length !== 1 ? "s" : ""}.
              </p>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="7Shifts access token"
                className="w-full border rounded px-3 py-2 text-sm"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleConfirmExport();
                }}
              />
            </div>
            <div className="flex justify-end gap-3 px-6 py-4 border-t bg-gray-50 rounded-b-lg">
              <button
                onClick={() => setShowTokenModal(false)}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmExport}
                disabled={!token.trim()}
                className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium"
              >
                Export to 7Shifts
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
