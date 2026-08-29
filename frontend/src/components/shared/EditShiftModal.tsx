import { useState } from "react";
import {
  editApprovedShifts,
  type ApprovedShiftEdit,
  type EditWarning,
  type WeekShift,
} from "../../api/approvedSchedules";
import { ApiError } from "../../api/client";
import { ScheduleLockedError } from "../../hooks/useScheduleStream";
import EmployeeSearchBox from "./EmployeeSearchBox";
import { getDayLabel } from "./ScheduleGrid";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, border } from "../../theme";
import type { Employee, Role } from "../../types";

interface Props {
  scheduleId: string;
  shift: WeekShift;
  employees: Employee[];
  roles: Role[];
  onClose: () => void;
  /** Called once an edit has actually applied (with or without warnings) so
   *  the parent can reload the week and pick up the new state. */
  onApplied: () => void;
}

/** Extract "HH:MM" from a wall-clock string, wherever the date/offset sit.
 *  Never round-trips through `Date` — that re-projects into the browser's
 *  timezone (issue #92). */
function extractTime(value: string): string {
  const afterDate = value.includes("T") ? value.slice(value.indexOf("T") + 1) : value;
  return afterDate.slice(0, 5);
}

/** Extract the trailing offset ("Z" or "+HH:MM"/"-HH:MM") from a wall-clock
 *  datetime string, or "" if it carries none (e.g. a naive test-DB value). */
function extractOffset(value: string): string {
  const m = value.match(/(Z|[+-]\d{2}:\d{2})$/);
  return m ? m[0] : "";
}

export default function EditShiftModal({
  scheduleId,
  shift,
  employees,
  roles,
  onClose,
  onApplied,
}: Props) {
  const { t } = useLanguage();

  const [employeeId, setEmployeeId] = useState(shift.employee_id);
  const [roleId, setRoleId] = useState(shift.role_id);
  const [startTime, setStartTime] = useState(extractTime(shift.start_time));
  const [endTime, setEndTime] = useState(extractTime(shift.end_time));

  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [warnings, setWarnings] = useState<EditWarning[]>([]);
  const [error, setError] = useState("");
  const [lockedBy, setLockedBy] = useState<string | null>(null);

  const handleError = (err: unknown) => {
    if (err instanceof ScheduleLockedError) {
      setLockedBy(err.lockedBy);
      return;
    }
    if (err instanceof ApiError) {
      const detail = err.data as { code?: string; message?: string } | null;
      if (err.status === 409 && detail?.code === "shift_locked_by_checkin") {
        setError(t.approvedSchedules.shiftLockedBody);
        return;
      }
      if (err.status === 400 && detail?.code === "invalid_edit") {
        setError(t.approvedSchedules.invalidEdit);
        return;
      }
      setError(detail?.message || err.message || "Save failed");
      return;
    }
    setError(err instanceof Error ? err.message : "Save failed");
  };

  const submit = async (edit: ApprovedShiftEdit, isDelete: boolean) => {
    setError("");
    setLockedBy(null);
    try {
      const result = await editApprovedShifts(scheduleId, [edit]);
      onApplied();
      if (result.warnings.length > 0) {
        setWarnings(result.warnings);
        setDeleted(isDelete);
      } else {
        onClose();
      }
    } catch (err) {
      handleError(err);
    }
  };

  const handleSave = async () => {
    setSubmitting(true);
    try {
      // ALWAYS send start_time and end_time together, even if only one
      // changed: the server computes its warnings against the shift's
      // existing span when either is missing, which would check a
      // different span than the one this edit actually applies.
      await submit(
        {
          shift_id: shift.id,
          employee_id: employeeId,
          role_id: roleId,
          start_time: `${shift.date}T${startTime}:00${extractOffset(shift.start_time)}`,
          end_time: `${shift.date}T${endTime}:00${extractOffset(shift.end_time)}`,
        },
        false
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await submit({ shift_id: shift.id, deleted: true }, true);
    } finally {
      setDeleting(false);
    }
  };

  const warningStyle = (code: EditWarning["code"]): string => {
    if (code === "already_booked") return "glass-alert-error";
    if (code === "no_availability") return "glass-alert-warning";
    return "glass-alert-info";
  };

  const warningTitle = (code: EditWarning["code"]): string => {
    if (code === "already_booked") return t.approvedSchedules.warningAlreadyBookedTitle;
    if (code === "no_availability") return t.approvedSchedules.warningNoAvailabilityTitle;
    return t.approvedSchedules.warningAlreadyExportedTitle;
  };

  const warningBody = (code: EditWarning["code"]): string => {
    if (code === "already_booked") return t.approvedSchedules.warningAlreadyBookedBody;
    if (code === "no_availability") return t.approvedSchedules.warningNoAvailabilityBody;
    return t.approvedSchedules.warningAlreadyExportedBody;
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h2 className={`text-lg font-semibold ${text.body}`}>
            {t.approvedSchedules.editTitle}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className={`${text.muted} hover:text-gray-300 text-xl leading-none`}
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          {warnings.length > 0 && (
            <div className="space-y-2">
              <p className={`text-sm font-semibold ${text.body}`}>
                {deleted
                  ? t.approvedSchedules.deletedWithWarnings
                  : t.approvedSchedules.savedWithWarnings}
              </p>
              {warnings.map((w, i) => (
                <div key={i} className={warningStyle(w.code)}>
                  <div className="font-medium">{warningTitle(w.code)}</div>
                  <div>{warningBody(w.code)}</div>
                  {w.detail && <div className="mt-1 text-xs opacity-80">{w.detail}</div>}
                </div>
              ))}
            </div>
          )}

          {lockedBy && (
            <div className="glass-alert-warning">
              <div className="font-medium">{t.approvedSchedules.scheduleLockedTitle}</div>
              <div>{t.approvedSchedules.scheduleLockedBody.replace("{name}", lockedBy)}</div>
            </div>
          )}

          {error && <div className="glass-alert-error">{error}</div>}

          {!deleted && (
            <>
              <div>
                <label className={`block text-sm font-medium ${text.body} mb-1`}>
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
                <label className={`block text-sm font-medium ${text.body} mb-1`}>
                  {t.common.role}
                </label>
                <select
                  value={roleId}
                  onChange={(e) => setRoleId(e.target.value)}
                  className="glass-input-sm w-full"
                >
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={`block text-sm font-medium ${text.body} mb-1`}>
                    {t.common.startTime}
                  </label>
                  <input
                    type="time"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    className="glass-input-sm w-full"
                  />
                </div>
                <div>
                  <label className={`block text-sm font-medium ${text.body} mb-1`}>
                    {t.common.endTime}
                  </label>
                  <input
                    type="time"
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    className="glass-input-sm w-full"
                  />
                </div>
              </div>

              <p className={`text-sm ${text.muted}`}>
                {t.common.date}: {getDayLabel(shift.date)} ({shift.date})
              </p>
            </>
          )}
        </div>

        <div className={`flex justify-between items-center px-6 py-4 border-t ${border.default}`}>
          {deleted ? (
            <div />
          ) : (
            <button
              type="button"
              onClick={handleDelete}
              disabled={submitting || deleting}
              className="px-3 py-2 bg-red-500/15 text-red-600 rounded-lg hover:bg-red-500/25 text-sm font-medium disabled:opacity-50"
            >
              {deleting ? t.common.saving : t.common.delete}
            </button>
          )}
          <div className="flex gap-2">
            {deleted ? (
              <button type="button" onClick={onClose} className="glass-btn-primary px-4 py-2 text-sm font-medium">
                {t.common.close}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={onClose}
                  className="glass-btn-secondary px-4 py-2 text-sm font-medium"
                >
                  {t.common.cancel}
                </button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={submitting || deleting || !employeeId || !roleId}
                  className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
                >
                  {submitting ? t.common.saving : t.common.save}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
