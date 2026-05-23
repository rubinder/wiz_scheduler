import { useMemo, useState } from "react";
import {
  createSpecialHoursDay,
  updateSpecialHoursDay,
} from "../../api/specialHours";
import { ApiError } from "../../api/client";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, border } from "../../theme";
import type { Location, ShiftTemplate, SpecialHoursDay } from "../../types";

interface Props {
  onClose: () => void;
  onSaved: (row: SpecialHoursDay) => void;
  editing: SpecialHoursDay | null;
  locations: Location[];
  // recurring templates only; keyed by location_id
  templatesByLocation: Record<string, ShiftTemplate[]>;
}

// Strip optional :ss from "HH:MM" or "HH:MM:SS" -> "HH:MM"
function trimSeconds(value: string): string {
  if (!value) return "";
  // Accept HH:MM or HH:MM:SS
  const parts = value.split(":");
  if (parts.length >= 2) {
    return `${parts[0]}:${parts[1]}`;
  }
  return value;
}

export default function SpecialHoursModal({
  onClose,
  onSaved,
  editing,
  locations,
  templatesByLocation,
}: Props) {
  const { t } = useLanguage();
  const isEdit = editing !== null;

  const [date, setDate] = useState<string>(editing?.date ?? "");
  const [locationId, setLocationId] = useState<string>(
    editing?.location_id ?? (locations[0]?.id ?? "")
  );
  const [openTime, setOpenTime] = useState<string>(
    trimSeconds(editing?.open_time ?? "09:00")
  );
  const [closeTime, setCloseTime] = useState<string>(
    trimSeconds(editing?.close_time ?? "17:00")
  );
  const [label, setLabel] = useState<string>(editing?.label ?? "");
  const [draftTemplateId, setDraftTemplateId] = useState<string>("");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>("");

  const availableTemplates = useMemo(
    () => templatesByLocation[locationId] ?? [],
    [templatesByLocation, locationId]
  );

  const locationName = (id: string): string =>
    locations.find((l) => l.id === id)?.name ?? id;

  const closeAfterOpenError =
    openTime && closeTime && closeTime <= openTime
      ? t.specialHours.closeAfterOpen
      : "";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (closeAfterOpenError) {
      setError(closeAfterOpenError);
      return;
    }
    if (!date || !locationId || !openTime || !closeTime) return;
    if (!isEdit && !draftTemplateId) {
      setError(
        t.specialHours.noRecurringTemplate.replace(
          "{location}",
          locationName(locationId)
        )
      );
      return;
    }

    setSubmitting(true);
    setError("");

    try {
      let result: SpecialHoursDay;
      if (isEdit && editing) {
        result = await updateSpecialHoursDay(editing.id, {
          date,
          open_time: openTime,
          close_time: closeTime,
          label: label.trim() ? label.trim() : null,
        });
      } else {
        result = await createSpecialHoursDay({
          location_id: locationId,
          date,
          open_time: openTime,
          close_time: closeTime,
          label: label.trim() ? label.trim() : null,
          draft_template_id: draftTemplateId || null,
        });
      }
      onSaved(result);
      onClose();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const detail = err.data as { code?: string; message?: string } | null;
        if (err.status === 400 && detail?.code === "no_recurring_template") {
          setError(
            t.specialHours.noRecurringTemplate.replace(
              "{location}",
              locationName(locationId)
            )
          );
        } else if (err.status === 409 && detail?.code === "duplicate") {
          setError(
            detail.message ||
              `${locationName(locationId)} · ${date}`
          );
        } else {
          setError(err.message || "Save failed");
        }
      } else {
        setError(err instanceof Error ? err.message : "Save failed");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <form onSubmit={handleSubmit}>
          <div
            className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}
          >
            <h2 className={`text-lg font-semibold ${text.body}`}>
              {isEdit
                ? t.specialHours.editTitle
                : t.specialHours.createTitle}
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
            {/* Date */}
            <div>
              <label className={`block text-sm font-medium ${text.body} mb-1`}>
                {t.specialHours.dateLabel}
              </label>
              <input
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className="glass-input-sm w-full"
              />
            </div>

            {/* Location */}
            <div>
              <label className={`block text-sm font-medium ${text.body} mb-1`}>
                {t.specialHours.locationLabel}
              </label>
              <select
                required
                value={locationId}
                onChange={(e) => {
                  setLocationId(e.target.value);
                  setDraftTemplateId(""); // reset template when location changes
                }}
                disabled={isEdit}
                className="glass-input-sm w-full disabled:opacity-60"
              >
                {locations.map((loc) => (
                  <option key={loc.id} value={loc.id}>
                    {loc.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Open / Close */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label
                  className={`block text-sm font-medium ${text.body} mb-1`}
                >
                  {t.specialHours.openLabel}
                </label>
                <input
                  type="time"
                  required
                  value={openTime}
                  onChange={(e) => setOpenTime(e.target.value)}
                  className="glass-input-sm w-full"
                />
              </div>
              <div>
                <label
                  className={`block text-sm font-medium ${text.body} mb-1`}
                >
                  {t.specialHours.closeLabel}
                </label>
                <input
                  type="time"
                  required
                  value={closeTime}
                  onChange={(e) => setCloseTime(e.target.value)}
                  className="glass-input-sm w-full"
                />
              </div>
            </div>
            {closeAfterOpenError && (
              <p className="text-xs text-red-500">{closeAfterOpenError}</p>
            )}

            {/* Label */}
            <div>
              <label className={`block text-sm font-medium ${text.body} mb-1`}>
                {t.specialHours.columnLabel}
              </label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={t.specialHours.labelPlaceholder}
                className="glass-input-sm w-full"
              />
            </div>

            {/* Draft template (create-only) */}
            {!isEdit && (
              <div>
                <label
                  className={`block text-sm font-medium ${text.body} mb-1`}
                >
                  {t.specialHours.draftTemplateLabel}
                </label>
                <select
                  required
                  value={draftTemplateId}
                  onChange={(e) => setDraftTemplateId(e.target.value)}
                  className="glass-input-sm w-full"
                >
                  <option value="">{t.common.select}</option>
                  {availableTemplates.map((tpl) => (
                    <option key={tpl.id} value={tpl.id}>
                      {tpl.name}
                    </option>
                  ))}
                </select>
                <p className={`text-xs ${text.muted} mt-1`}>
                  {t.specialHours.draftTemplateHelp}
                </p>
                {availableTemplates.length === 0 && (
                  <p className="text-xs text-red-500 mt-1">
                    {t.specialHours.noRecurringTemplate.replace(
                      "{location}",
                      locationName(locationId)
                    )}
                  </p>
                )}
              </div>
            )}

            {error && <div className="glass-alert-error">{error}</div>}
          </div>

          <div
            className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}
          >
            <button
              type="button"
              onClick={onClose}
              className="glass-btn-secondary px-4 py-2 text-sm font-medium"
            >
              {t.specialHours.cancelButton}
            </button>
            <button
              type="submit"
              disabled={
                submitting ||
                !!closeAfterOpenError ||
                !date ||
                !locationId ||
                !openTime ||
                !closeTime ||
                (!isEdit && !draftTemplateId)
              }
              className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
            >
              {submitting
                ? t.specialHours.savingButton
                : t.specialHours.saveButton}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
