import { useCallback, useEffect, useMemo, useState } from "react";
import * as condensedRolesApi from "../../api/condensedRoles";
import * as rolesApi from "../../api/roles";
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
      setError("Name is required");
      return;
    }
    if (formRoleIds.length < 2) {
      setError("Select at least 2 roles to group");
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
          <h1 className="text-2xl font-bold">Role Equivalents</h1>
          <p className="text-sm text-gray-500 mt-1">
            Group roles that serve the same scheduling purpose. Employees with
            any role in a group can fill shifts for any other role in that group.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={startCreate}
            className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
          >
            + New Group
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {/* Create / Edit form */}
      {showForm && (
        <div className="mb-6 bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">
            {editingId ? "Edit Group" : "Create Group"}
          </h2>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Condensed Role Name
            </label>
            <input
              type="text"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. Server, Host, Line Cook"
              className="w-full max-w-md border border-gray-300 rounded px-3 py-2 text-sm"
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Roles in this group
              <span className="text-gray-400 font-normal ml-1">
                (select 2 or more)
              </span>
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 max-h-64 overflow-y-auto border border-gray-200 rounded p-3">
              {availableRoles.length === 0 && !formRoleIds.length && (
                <p className="text-sm text-gray-400 col-span-full">
                  All roles are already assigned to groups.
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
                      className={`text-left px-3 py-2 rounded border text-sm transition-colors ${
                        selected
                          ? "bg-indigo-100 border-indigo-400 text-indigo-800 font-medium"
                          : "bg-white border-gray-200 text-gray-700 hover:border-gray-400"
                      }`}
                    >
                      {role.name}
                      {role.external_id && <span className="block text-xs text-gray-400 font-normal truncate">{role.external_id}</span>}
                    </button>
                  );
                })}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
            >
              {editingId ? "Update" : "Create"}
            </button>
            <button
              onClick={resetForm}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Suggestions */}
      {suggestions.length > 0 && !showForm && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Suggested Groups
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            {suggestions.map((s) => (
              <button
                key={s.name}
                onClick={() => applySuggestion(s)}
                className="text-left bg-amber-50 border border-amber-200 rounded-lg p-4 hover:border-amber-400 hover:bg-amber-100 transition-colors"
              >
                <div className="font-semibold text-amber-900 mb-2">
                  {s.name}
                </div>
                <div className="flex flex-wrap gap-1">
                  {s.roles.map((r) => (
                    <span
                      key={r.id}
                      className="inline-block px-2 py-0.5 bg-amber-100 text-amber-800 rounded text-xs"
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
            className="bg-white rounded-lg shadow p-4 flex items-start justify-between"
          >
            <div>
              <h3 className="font-semibold text-gray-900">{cr.name}</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                {cr.roles.map((r) => (
                  <span
                    key={r.role_id}
                    className="inline-block px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs font-medium"
                  >
                    {r.role_name}
                    {(() => { const ext = roles.find((rl) => rl.id === r.role_id)?.external_id; return ext ? <span className="ml-1 text-gray-400 font-normal">{ext}</span> : null; })()}
                  </span>
                ))}
              </div>
            </div>
            <div className="flex gap-2 ml-4 shrink-0">
              <button
                onClick={() => startEdit(cr)}
                className="text-indigo-600 hover:text-indigo-800 text-sm font-medium"
              >
                Edit
              </button>
              <button
                onClick={() => handleDelete(cr.id)}
                className="text-red-600 hover:text-red-800 text-sm font-medium"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
        {condensedRoles.length === 0 && !showForm && (
          <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
            No role equivalents configured. Create a group to let employees with
            similar roles fill each other's shifts.
          </div>
        )}
      </div>
    </div>
  );
}
