import { useCallback, useEffect, useMemo, useState } from "react";
import * as condensedRolesApi from "../../api/condensedRoles";
import * as rolesApi from "../../api/roles";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, action } from "../../theme";
import DemoGuard from "../../components/shared/DemoGuard";
import type { CondensedRole, Role } from "../../types";

interface Suggestion {
  name: string;
  roles: Role[];
}

/** Normalize a role name to a base form for grouping.
 *  "1 Server" / "2 Server" -> "server"
 *  "Host 1" / "Host 2" -> "host"
 *  "Lead Line Cook" / "Line Cook" -> "line cook"
 */
function normalizeRoleName(name: string): string {
  let s = name.toLowerCase().trim();
  // Strip leading/trailing numbers and whitespace: "1 Server" -> "server", "Host 2" -> "host"
  s = s.replace(/^\d+\s+/, "").replace(/\s+\d+$/, "");
  // Strip common prefixes
  for (const prefix of ["lead ", "senior ", "junior ", "assistant ", "head "]) {
    if (s.startsWith(prefix)) {
      s = s.slice(prefix.length);
      break;
    }
  }
  return s.trim();
}

export default function RoleEquivalents() {
  const { t } = useLanguage();
  const [condensedRoles, setCondensedRoles] = useState<CondensedRole[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState("");

  // Form state for creating / editing
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formRoleIds, setFormRoleIds] = useState<string[]>([]);
  const [showForm, setShowForm] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [cr, r] = await Promise.all([
        condensedRolesApi.listCondensedRoles(),
        rolesApi.listRoles(),
      ]);
      setCondensedRoles(cr);
      setRoles(r);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Roles already assigned to a condensed role (excluding the one being edited)
  const assignedRoleIds = new Set(
    condensedRoles
      .filter((cr) => cr.id !== editingId)
      .flatMap((cr) => cr.roles.map((r) => r.role_id))
  );

  const availableRoles = roles.filter((r) => !assignedRoleIds.has(r.id));

  // Build suggestions: group unassigned roles by normalized name
  const suggestions: Suggestion[] = useMemo(() => {
    const groups = new Map<string, Role[]>();
    for (const role of availableRoles) {
      const key = normalizeRoleName(role.name);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(role);
    }
    // Only suggest groups with 2+ roles
    return Array.from(groups.entries())
      .filter(([, roles]) => roles.length >= 2)
      .map(([key, roles]) => ({
        name: key.charAt(0).toUpperCase() + key.slice(1),
        roles: roles.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => b.roles.length - a.roles.length);
  }, [availableRoles]);

  const applySuggestion = (s: Suggestion) => {
    setEditingId(null);
    setFormName(s.name);
    setFormRoleIds(s.roles.map((r) => r.id));
    setShowForm(true);
  };

  const resetForm = () => {
    setEditingId(null);
    setFormName("");
    setFormRoleIds([]);
    setShowForm(false);
  };

  const startEdit = (cr: CondensedRole) => {
    setEditingId(cr.id);
    setFormName(cr.name);
    setFormRoleIds(cr.roles.map((r) => r.role_id));
    setShowForm(true);
  };

  const startCreate = () => {
    resetForm();
    setShowForm(true);
  };

  const toggleRole = (roleId: string) => {
    setFormRoleIds((prev) =>
      prev.includes(roleId)
        ? prev.filter((id) => id !== roleId)
        : [...prev, roleId]
    );
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setError(t.roleEquivalents.nameRequired);
      return;
    }
    if (formRoleIds.length < 2) {
      setError(t.roleEquivalents.selectAtLeast2);
      return;
    }
    setError("");
    try {
      if (editingId) {
        await condensedRolesApi.updateCondensedRole(editingId, {
          name: formName,
          role_ids: formRoleIds,
        });
      } else {
        await condensedRolesApi.createCondensedRole({
          name: formName,
          role_ids: formRoleIds,
        });
      }
      resetForm();
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await condensedRolesApi.deleteCondensedRole(id);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className={`text-2xl font-bold ${text.heading}`}>{t.roleEquivalents.title}</h1>
          <p className={`text-sm ${text.muted} mt-1`}>
            {t.roleEquivalents.description}
          </p>
        </div>
        {!showForm && (
          <DemoGuard>
            <button
              onClick={startCreate}
              className="glass-btn-primary"
            >
              {t.roleEquivalents.newGroup}
            </button>
          </DemoGuard>
        )}
      </div>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      {/* Create / Edit form */}
      {showForm && (
        <div className="glass-card p-6 mb-6">
          <h2 className={`text-lg font-semibold ${text.heading} mb-4`}>
            {editingId ? t.roleEquivalents.editGroup : t.roleEquivalents.createGroup}
          </h2>
          <div className="mb-4">
            <label className="glass-label">
              {t.roleEquivalents.condensedRoleName}
            </label>
            <input
              type="text"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder={t.roleEquivalents.namePlaceholder}
              className="glass-input w-full max-w-md"
            />
          </div>
          <div className="mb-4">
            <label className="glass-label mb-2">
              {t.roleEquivalents.rolesInGroup}
              <span className={`${text.muted} font-normal ms-1`}>
                {t.roleEquivalents.selectTwoOrMore}
              </span>
            </label>
            <div className={`grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 max-h-64 overflow-y-auto border ${border.default} rounded-lg p-3`}>
              {availableRoles.length === 0 && !formRoleIds.length && (
                <p className={`text-sm ${text.muted} col-span-full`}>
                  {t.roleEquivalents.allAssigned}
                </p>
              )}
              {roles
                .filter(
                  (r) => !assignedRoleIds.has(r.id) || formRoleIds.includes(r.id)
                )
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((role) => {
                  const selected = formRoleIds.includes(role.id);
                  return (
                    <button
                      key={role.id}
                      onClick={() => toggleRole(role.id)}
                      className={`text-start px-3 py-2 rounded-lg border text-sm transition-colors ${
                        selected
                          ? "bg-accent/15 border-accent/30 text-accent-dark font-medium"
                          : `bg-white/50 ${border.default} ${text.secondary} hover:border-sage/30`
                      }`}
                    >
                      {role.name}
                      {role.external_id && <span className={`block text-xs ${text.muted} font-normal truncate`}>{role.external_id}</span>}
                    </button>
                  );
                })}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              className="glass-btn-primary"
            >
              {editingId ? t.common.update : t.common.create}
            </button>
            <button
              onClick={resetForm}
              className="glass-btn-secondary"
            >
              {t.common.cancel}
            </button>
          </div>
        </div>
      )}

      {/* Suggestions */}
      {suggestions.length > 0 && !showForm && (
        <div className="mb-6">
          <h2 className={`text-sm font-semibold ${text.muted} uppercase tracking-wide mb-3`}>
            {t.roleEquivalents.suggestedGroups}
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            {suggestions.map((s) => (
              <button
                key={s.name}
                onClick={() => applySuggestion(s)}
                className="text-start bg-amber-50 border border-amber-200 rounded-xl p-4 hover:border-amber-300 hover:bg-amber-100 transition-colors"
              >
                <div className="font-semibold text-amber-700 mb-2">
                  {s.name}
                </div>
                <div className="flex flex-wrap gap-1">
                  {s.roles.map((r) => (
                    <span
                      key={r.id}
                      className="inline-block px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-xs"
                    >
                      {r.name}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Existing groups */}
      <div className="space-y-4">
        {condensedRoles.map((cr) => (
          <div
            key={cr.id}
            className="glass-card p-4 flex items-start justify-between"
          >
            <div>
              <h3 className={`${text.heading} font-semibold`}>{cr.name}</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {cr.roles.map((r) => (
                  <span
                    key={r.role_id}
                    className={`inline-block px-2 py-1 ${bg.tableHeader} ${text.secondary} rounded text-xs font-medium`}
                  >
                    {r.role_name}
                    {(() => { const ext = roles.find((rl) => rl.id === r.role_id)?.external_id; return ext ? <span className={`ms-1 ${text.muted} font-normal`}>{ext}</span> : null; })()}
                  </span>
                ))}
              </div>
            </div>
            <div className="flex gap-2 ms-4 shrink-0">
              <button
                onClick={() => startEdit(cr)}
                className={action.edit}
              >
                {t.common.edit}
              </button>
              <button
                onClick={() => handleDelete(cr.id)}
                className={action.delete}
              >
                {t.common.delete}
              </button>
            </div>
          </div>
        ))}
        {condensedRoles.length === 0 && !showForm && (
          <div className={`glass-card p-8 text-center ${text.muted}`}>
            {t.roleEquivalents.emptyState}
          </div>
        )}
      </div>
    </div>
  );
}
