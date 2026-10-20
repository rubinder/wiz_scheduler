import { useState } from "react";
import { useLanguage } from "../../i18n/LanguageContext";
import { getDayLabel } from "./ScheduleGrid";
import { text, border } from "../../theme";

interface Props {
  roles: { id: string; name: string }[];
  /** The week's dates ("YYYY-MM-DD") to choose from. */
  dates: string[];
  onSubmit: (v: { role_id: string; role_name: string; date: string; start: string; end: string }) =>
    | Promise<void>
    | void;
  onClose: () => void;
}

/** Add a shift with no one on it yet (#174). */
export default function AddOpenShiftModal({ roles, dates, onSubmit, onClose }: Props) {
  const { t } = useLanguage();
  const [roleId, setRoleId] = useState(roles[0]?.id ?? "");
  const [date, setDate] = useState(dates[0] ?? "");
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("17:00");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const role = roles.find((r) => r.id === roleId);
    if (!role || !date) return;
    setSaving(true);
    setError("");
    try {
      await onSubmit({ role_id: role.id, role_name: role.name, date, start, end });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h3 className={`text-lg font-semibold ${text.body}`}>{t.schedule.addOpenShiftTitle}</h3>
          <button type="button" onClick={onClose} className={`${text.muted} text-xl leading-none`}>
            &times;
          </button>
        </div>
        <div className="px-6 py-4 space-y-4">
          {error && <div className="glass-alert-error">{error}</div>}
          <div>
            <label className="glass-label">{t.common.role}</label>
            <select value={roleId} onChange={(e) => setRoleId(e.target.value)} className="glass-input w-full">
              {roles.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="glass-label">{t.common.date}</label>
            <select value={date} onChange={(e) => setDate(e.target.value)} className="glass-input w-full">
              {dates.map((d) => (
                <option key={d} value={d}>{getDayLabel(d)} ({d})</option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="glass-label">{t.common.startTime}</label>
              <input type="time" value={start} onChange={(e) => setStart(e.target.value)} className="glass-input w-full" />
            </div>
            <div>
              <label className="glass-label">{t.common.endTime}</label>
              <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} className="glass-input w-full" />
            </div>
          </div>
        </div>
        <div className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}>
          <button type="button" onClick={onClose} className="glass-btn-secondary text-sm font-medium">
            {t.common.cancel}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={saving || !roleId || !date}
            className="glass-btn-primary text-sm font-medium disabled:opacity-50"
          >
            {saving ? t.common.saving : t.common.save}
          </button>
        </div>
      </div>
    </div>
  );
}
