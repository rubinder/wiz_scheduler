import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  deleteSpecialHoursDay,
  listSpecialHours,
} from "../../api/specialHours";
import { listLocations } from "../../api/locations";
import { listShiftTemplates } from "../../api/shiftTemplates";
import SpecialHoursModal from "../../components/shared/SpecialHoursModal";
import type {
  Location,
  ShiftTemplate,
  SpecialHoursDay,
} from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, action } from "../../theme";

function trimSeconds(value: string): string {
  if (!value) return "";
  const parts = value.split(":");
  if (parts.length >= 2) {
    return `${parts[0]}:${parts[1]}`;
  }
  return value;
}

export default function SpecialHours() {
  const { t } = useLanguage();

  const [entries, setEntries] = useState<SpecialHoursDay[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [templates, setTemplates] = useState<ShiftTemplate[]>([]);
  const [locationFilter, setLocationFilter] = useState<string>("all");
  const [editing, setEditing] = useState<SpecialHoursDay | null>(null);
  const [creating, setCreating] = useState<boolean>(false);
  // Reserved for Task 8: duplicate-to-other-locations modal.
  const [, setDuplicating] = useState<SpecialHoursDay | null>(null);
  const [, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string>("");

  const fetchEntries = useCallback(async (filter: string) => {
    try {
      const params =
        filter && filter !== "all" ? { location_id: filter } : undefined;
      const rows = await listSpecialHours(params);
      setEntries(rows);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load entries");
    }
  }, []);

  const fetchInitial = useCallback(async () => {
    try {
      const [rows, locs, tpls] = await Promise.all([
        listSpecialHours(),
        listLocations(),
        listShiftTemplates(),
      ]);
      setEntries(rows);
      setLocations(locs);
      setTemplates(tpls);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchInitial();
  }, [fetchInitial]);

  useEffect(() => {
    // Skip the initial render's redundant fetch (fetchInitial already loads).
    // We only refetch when the filter changes after mount.
    fetchEntries(locationFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationFilter]);

  const templatesByLocation = useMemo(() => {
    const out: Record<string, ShiftTemplate[]> = {};
    for (const tpl of templates) {
      if (!out[tpl.location_id]) out[tpl.location_id] = [];
      out[tpl.location_id].push(tpl);
    }
    return out;
  }, [templates]);

  const locationName = (id: string): string =>
    locations.find((l) => l.id === id)?.name ?? id.slice(0, 8);

  const templateName = (id: string | null): string => {
    if (!id) return "—";
    return templates.find((tpl) => tpl.id === id)?.name ?? id.slice(0, 8);
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm(t.specialHours.deleteConfirm)) return;
    try {
      await deleteSpecialHoursDay(id);
      await fetchEntries(locationFilter);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleSaved = async () => {
    setNotice(null);
    await fetchEntries(locationFilter);
  };

  const closeModal = () => {
    setCreating(false);
    setEditing(null);
  };

  const modalOpen = creating || editing !== null;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.specialHours.title}
        </h1>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <label className={`text-sm ${text.muted}`}>
              {t.specialHours.filterLocationLabel}
            </label>
            <select
              value={locationFilter}
              onChange={(e) => setLocationFilter(e.target.value)}
              className={`glass-input-sm text-sm ${border.default} ${text.primary}`}
            >
              <option value="all">
                {t.specialHours.filterAllLocations}
              </option>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="glass-btn-primary"
          >
            + {t.specialHours.addButton}
          </button>
        </div>
      </div>

      <p className={`text-sm ${text.muted} mb-6`}>
        {t.specialHours.description}
      </p>

      {error && <div className="glass-alert-error mb-4">{error}</div>}

      {entries.length === 0 ? (
        <div className={`glass-card p-6 text-sm ${text.muted}`}>
          {t.specialHours.noEntries}
        </div>
      ) : (
        <div className="glass-card overflow-x-auto">
          <table className={`min-w-full divide-y ${border.divider}`}>
            <thead className={bg.tableHeader}>
              <tr>
                <th
                  className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnDate}
                </th>
                <th
                  className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnLocation}
                </th>
                <th
                  className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnHours}
                </th>
                <th
                  className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnLabel}
                </th>
                <th
                  className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnTemplate}
                </th>
                <th
                  className={`px-4 py-3 text-right text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.specialHours.columnActions}
                </th>
              </tr>
            </thead>
            <tbody className={`divide-y ${border.divider}`}>
              {entries.map((row) => (
                <tr key={row.id}>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.body}`}>{row.date}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.body}`}>
                      {locationName(row.location_id)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.body}`}>
                      {trimSeconds(row.open_time)} – {trimSeconds(row.close_time)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.secondary}`}>
                      {row.label ?? ""}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    {row.shift_template_id ? (
                      <Link
                        to="/manager/shift-templates"
                        className={action.link}
                      >
                        {templateName(row.shift_template_id)}
                      </Link>
                    ) : (
                      <span className={`text-sm ${text.muted}`}>—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right space-x-3">
                    <button
                      onClick={() => setEditing(row)}
                      className={action.edit}
                    >
                      {t.specialHours.editAction}
                    </button>
                    <button
                      onClick={() => setDuplicating(row)}
                      className={action.edit}
                    >
                      {t.specialHours.duplicateAction}
                    </button>
                    <button
                      onClick={() => handleDelete(row.id)}
                      className={action.delete}
                    >
                      {t.specialHours.deleteAction}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {modalOpen && (
        <SpecialHoursModal
          onClose={closeModal}
          onSaved={handleSaved}
          editing={editing}
          locations={locations}
          templatesByLocation={templatesByLocation}
        />
      )}
    </div>
  );
}
