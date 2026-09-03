import { useEffect, useMemo, useState } from "react";
import {
  createAffinity,
  deleteAffinity,
  listAffinities,
  updateAffinity,
} from "../../api/affinities";
import { listEmployees } from "../../api/employees";
import EmployeeSearchBox from "../../components/shared/EmployeeSearchBox";
import { useLanguage } from "../../i18n/LanguageContext";
import type {
  Employee,
  EmployeeAffinity,
} from "../../types";
import { text, bg, border } from "../../theme";
import {
  getLevelOptions,
  getLevelLabel,
  getLevelColor,
} from "./_employeesShared";

interface EditingRow {
  id: string | null;
  employee_id: string;
  target_employee_id: string;
  level: string;
  entry_date: string;
  expiration_date: string;
}

// ── Main component ──

export default function EmployeeAssociation() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Affinities state
  const [affinities, setAffinities] = useState<EmployeeAffinity[]>([]);
  const [editing, setEditing] = useState<EditingRow | null>(null);
  const [saving, setSaving] = useState(false);

  const employeeMap = useMemo(() => {
    const map = new Map<string, string>();
    employees.forEach((e) => map.set(e.id, e.full_name));
    return map;
  }, [employees]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [affinityData, employeeData] = await Promise.all([
        listAffinities(),
        listEmployees(),
      ]);
      setAffinities(affinityData);
      setEmployees(employeeData);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // ── Affinity handlers ──

  const today = new Date().toISOString().split("T")[0];

  const startAdding = () => {
    setEditing({
      id: null,
      employee_id: "",
      target_employee_id: "",
      level: "0.5",
      entry_date: today,
      expiration_date: "",
    });
  };

  const startEditing = (a: EmployeeAffinity) => {
    setEditing({
      id: a.id,
      employee_id: a.employee_id,
      target_employee_id: a.target_employee_id,
      level: String(a.level),
      entry_date: a.entry_date,
      expiration_date: a.expiration_date ?? "",
    });
  };

  const cancelEditing = () => setEditing(null);

  const handleSave = async () => {
    if (!editing) return;
    if (editing.employee_id === editing.target_employee_id) {
      setError("Employee and target must be different");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        employee_id: editing.employee_id,
        target_employee_id: editing.target_employee_id,
        level: parseFloat(editing.level),
        entry_date: editing.entry_date,
        expiration_date: editing.expiration_date || null,
      };
      if (editing.id) {
        await updateAffinity(editing.id, payload);
      } else {
        await createAffinity(payload);
      }
      setEditing(null);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t.association.deleteConfirm)) return;
    try {
      await deleteAffinity(id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const levelOptions = useMemo(() => getLevelOptions(t), [t]);

  // ── Render ──

  if (loading) {
    return (
      <div className={`flex items-center justify-center h-64 ${text.muted}`}>
        {t.common.loading}
      </div>
    );
  }

  if (employees.length === 0) {
    return (
      <div className={`text-center py-16 ${text.muted}`}>
        <p className="text-lg">{t.association.noEmployees}</p>
        <p className="mt-2">
          {t.association.addEmployeesFirst}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.association.title}
        </h1>
        <p className={`mt-1 text-sm ${text.muted}`}>
          {t.association.description}{" "}
          {t.association.affinityExplanation} <strong className={text.body}>1</strong> {t.association.mustTogether}{" "}
          <strong className={text.body}>0.5</strong> {t.association.preferTogether}{" "}
          <strong className={text.body}>-1</strong> {t.association.cannotTogether}{" "}
          <strong className={text.body}>-0.5 / -0.7</strong> {t.association.preferApart}
        </p>
      </div>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      <div>
        <div className="flex justify-end mb-4">
          <button
            onClick={startAdding}
            disabled={editing !== null}
            className="glass-btn-primary disabled:opacity-50"
          >
            {t.association.addAssociation}
          </button>
        </div>

        <div className="glass-card overflow-hidden">
          <table className="glass-table">
            <thead className={bg.tableHeader}>
              <tr>
                <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                  {t.common.employee}
                </th>
                <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                  {t.association.targetEmployee}
                </th>
                <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                  {t.association.affinityLevel}
                </th>
                <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                  {t.association.entryDate}
                </th>
                <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase`}>
                  {t.association.expirationDate}
                </th>
                <th className={`px-4 py-3 text-end text-xs font-medium ${text.muted} uppercase`}>
                  {t.common.actions}
                </th>
              </tr>
            </thead>
            <tbody className={`divide-y ${border.divider}`}>
              {editing && (
                <tr className={`${bg.editingRow} align-top`}>
                  <td className="px-4 py-4">
                    <EmployeeSearchBox
                      employees={employees}
                      value={editing.employee_id}
                      onChange={(id) =>
                        setEditing({ ...editing, employee_id: id })
                      }
                      excludeIds={
                        editing.target_employee_id
                          ? [editing.target_employee_id]
                          : []
                      }
                      placeholder={t.association.typeToSearch}
                      inline
                    />
                  </td>
                  <td className="px-4 py-4">
                    <EmployeeSearchBox
                      employees={employees}
                      value={editing.target_employee_id}
                      onChange={(id) =>
                        setEditing({ ...editing, target_employee_id: id })
                      }
                      excludeIds={
                        editing.employee_id ? [editing.employee_id] : []
                      }
                      placeholder={t.association.typeToSearch}
                      inline
                    />
                  </td>
                  <td className="px-4 py-4">
                    <select
                      value={editing.level}
                      onChange={(e) =>
                        setEditing({ ...editing, level: e.target.value })
                      }
                      className="glass-input-sm w-full"
                    >
                      {levelOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-4">
                    <input
                      type="date"
                      value={editing.entry_date}
                      onChange={(e) =>
                        setEditing({ ...editing, entry_date: e.target.value })
                      }
                      className="glass-input-sm w-full"
                    />
                  </td>
                  <td className="px-4 py-4">
                    <input
                      type="date"
                      value={editing.expiration_date}
                      onChange={(e) =>
                        setEditing({
                          ...editing,
                          expiration_date: e.target.value,
                        })
                      }
                      className="glass-input-sm w-full"
                      placeholder="Optional"
                    />
                  </td>
                  <td className="px-4 py-4 text-end space-x-2">
                    <button
                      onClick={handleSave}
                      disabled={
                        saving ||
                        !editing.employee_id ||
                        !editing.target_employee_id ||
                        !editing.entry_date
                      }
                      className="glass-btn-success px-3 py-1 text-xs disabled:opacity-50"
                    >
                      {saving ? t.common.saving : t.common.save}
                    </button>
                    <button
                      onClick={cancelEditing}
                      className="glass-btn-secondary px-3 py-1 text-xs"
                    >
                      {t.common.cancel}
                    </button>
                  </td>
                </tr>
              )}

              {affinities.length === 0 && !editing && (
                <tr>
                  <td
                    colSpan={6}
                    className={`px-4 py-8 text-center ${text.muted} text-sm`}
                  >
                    {t.association.noAssociations} &quot;{t.association.addAssociation}&quot;
                    {" "}{t.association.toCreateOne}
                  </td>
                </tr>
              )}

              {affinities.map((a) =>
                editing?.id === a.id ? null : (
                  <tr key={a.id} className="hover:bg-sage/[0.05]">
                    <td className={`px-4 py-5 text-sm ${text.body}`}>
                      {employeeMap.get(a.employee_id) ?? a.employee_id}
                    </td>
                    <td className={`px-4 py-5 text-sm ${text.body}`}>
                      {employeeMap.get(a.target_employee_id) ??
                        a.target_employee_id}
                    </td>
                    <td className="px-4 py-5 text-sm">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${getLevelColor(a.level)}`}
                      >
                        {a.level} — {getLevelLabel(a.level, t)}
                      </span>
                    </td>
                    <td className={`px-4 py-5 text-sm ${text.secondary}`}>
                      {a.entry_date}
                    </td>
                    <td className={`px-4 py-5 text-sm ${text.secondary}`}>
                      {a.expiration_date ?? (
                        <span className="text-gray-500">{t.common.none}</span>
                      )}
                    </td>
                    <td className="px-4 py-5 text-end space-x-2">
                      <button
                        onClick={() => startEditing(a)}
                        disabled={editing !== null}
                        className="px-3 py-1 bg-accent/10 text-accent-dark text-xs rounded hover:bg-accent/20 disabled:opacity-50"
                      >
                        {t.common.edit}
                      </button>
                      <button
                        onClick={() => handleDelete(a.id)}
                        disabled={editing !== null}
                        className="px-3 py-1 bg-red-500/15 text-red-300 text-xs rounded hover:bg-red-500/25 disabled:opacity-50"
                      >
                        {t.common.delete}
                      </button>
                    </td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
