import { useState } from "react";
import { createSpecialHoursDay } from "../../api/specialHours";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, border } from "../../theme";
import type { Location, SpecialHoursDay } from "../../types";

interface Props {
  source: SpecialHoursDay;
  /** All selectable locations (typically every location EXCEPT source.location_id). */
  locations: Location[];
  onClose: () => void;
  onCompleted: (
    created: SpecialHoursDay[],
    errors: { location_id: string; message: string }[],
  ) => void;
}

export default function DuplicateSpecialHoursModal({
  source,
  locations,
  onClose,
  onCompleted,
}: Props) {
  const { t } = useLanguage();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    const created: SpecialHoursDay[] = [];
    const errors: { location_id: string; message: string }[] = [];
    await Promise.all(
      Array.from(selected).map(async (loc_id) => {
        try {
          const row = await createSpecialHoursDay({
            location_id: loc_id,
            date: source.date,
            open_time: source.open_time,
            close_time: source.close_time,
            label: source.label,
            // No draft_template_id — server picks the location's recurring.
          });
          created.push(row);
        } catch (err: unknown) {
          const message =
            err instanceof Error ? err.message : "Failed";
          errors.push({ location_id: loc_id, message });
        }
      }),
    );
    setSubmitting(false);
    onCompleted(created, errors);
    onClose();
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div
          className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}
        >
          <h2 className={`text-lg font-semibold ${text.body}`}>
            {t.specialHours.duplicateTitle}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className={`${text.muted} hover:text-gray-300 text-xl leading-none`}
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-3">
          <p className={`text-sm ${text.muted}`}>
            {t.specialHours.duplicateHelp}
          </p>
          {locations.length === 0 ? (
            <p className={`text-sm ${text.muted}`}>
              {t.specialHours.filterAllLocations}
            </p>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {locations.map((loc) => (
                <label
                  key={loc.id}
                  className="flex items-center gap-2 text-sm cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(loc.id)}
                    onChange={() => toggle(loc.id)}
                  />
                  <span className={text.body}>{loc.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        <div
          className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}
        >
          <button
            type="button"
            onClick={onClose}
            className="glass-btn-secondary px-4 py-2 text-sm font-medium"
            disabled={submitting}
          >
            {t.specialHours.cancelButton}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting || selected.size === 0}
            className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {submitting
              ? t.specialHours.savingButton
              : `${t.specialHours.duplicateAction} (${selected.size})`}
          </button>
        </div>
      </div>
    </div>
  );
}
