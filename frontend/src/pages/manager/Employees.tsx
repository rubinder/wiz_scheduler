import { useCallback, useEffect, useState } from "react";
import * as employeesApi from "../../api/employees";
import * as rolesApi from "../../api/roles";
import * as locationsApi from "../../api/locations";
import * as companyApi from "../../api/company";
import DemoGuard from "../../components/shared/DemoGuard";
import ImportModal from "../../components/shared/ImportModal";
import type {
  Company,
  Employee,
  Location,
  Role,
} from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, action, badge } from "../../theme";

interface RoleAssignment {
  role_id: string;
  skill_level: number;
}

export default function Employees() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [error, setError] = useState("");
  const [showImportModal, setShowImportModal] = useState(false);

  // Editing state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<{
    full_name: string;
    email: string;
    roles: RoleAssignment[];
    location_ids: string[];
    company_ids: string[];
  }>({ full_name: "", email: "", roles: [], location_ids: [], company_ids: [] });

  // Add-row state
  const [showAddRow, setShowAddRow] = useState(false);
  const [addValues, setAddValues] = useState<{
    full_name: string;
    email: string;
    roles: RoleAssignment[];
    location_ids: string[];
    company_ids: string[];
  }>({ full_name: "", email: "", roles: [], location_ids: [], company_ids: [] });

  const fetchData = useCallback(async () => {
    try {
      const [emps, rls, locs, comps] = await Promise.all([
        employeesApi.listEmployees(),
        rolesApi.listRoles(),
        locationsApi.listLocations(),
        companyApi.listGroupCompanies().catch(() => [] as Company[]),
      ]);
      setEmployees(emps);
      setRoles(rls);
      setLocations(locs);
      setCompanies(comps);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const roleName = (roleId: string) =>
    roles.find((r) => r.id === roleId)?.name ?? roleId;

  const locationName = (locId: string) =>
    locations.find((l) => l.id === locId)?.name ?? locId.slice(0, 8);

  const companyName = (compId: string) =>
    companies.find((c) => c.id === compId)?.name ?? compId.slice(0, 8);

  const startEdit = (emp: Employee) => {
    setEditingId(emp.id);
    setEditValues({
      full_name: emp.full_name,
      email: emp.email ?? "",
      roles: emp.roles.map((r) => ({
        role_id: r.role_id,
        skill_level: r.skill_level,
      })),
      location_ids: emp.location_ids ?? [],
      company_ids: emp.company_ids ?? [],
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditValues({
      full_name: "",
      email: "",
      roles: [],
      location_ids: [],
      company_ids: [],
    });
  };

  const handleSave = async () => {
    if (!editingId) return;
    try {
      await employeesApi.updateEmployee(editingId, {
        full_name: editValues.full_name,
        email: editValues.email || null,
        roles: editValues.roles,
        location_ids:
          editValues.location_ids.length > 0
            ? editValues.location_ids
            : null,
        company_ids:
          editValues.company_ids.length > 0
            ? editValues.company_ids
            : undefined,
      });
      setEditingId(null);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await employeesApi.deleteEmployee(id);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleCreate = async () => {
    try {
      await employeesApi.createEmployee({
        full_name: addValues.full_name,
        email: addValues.email || null,
        roles: addValues.roles.length > 0 ? addValues.roles : undefined,
        location_ids:
          addValues.location_ids.length > 0
            ? addValues.location_ids
            : undefined,
        company_ids:
          addValues.company_ids.length > 0
            ? addValues.company_ids
            : undefined,
      });
      setAddValues({
        full_name: "",
        email: "",
        roles: [],
        location_ids: [],
        company_ids: [],
      });
      setShowAddRow(false);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  const handleImportUpload = async (file: File) => {
    const result = await employeesApi.bulkUploadEmployees(file);
    await fetchData();
    return result;
  };

  const toggleRole = (
    currentRoles: RoleAssignment[],
    roleId: string
  ): RoleAssignment[] => {
    const exists = currentRoles.find((r) => r.role_id === roleId);
    if (exists) {
      return currentRoles.filter((r) => r.role_id !== roleId);
    }
    return [...currentRoles, { role_id: roleId, skill_level: 3 }];
  };

  const updateSkillLevel = (
    currentRoles: RoleAssignment[],
    roleId: string,
    level: number
  ): RoleAssignment[] =>
    currentRoles.map((r) =>
      r.role_id === roleId ? { ...r, skill_level: level } : r
    );

  const toggleId = (list: string[], id: string): string[] =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  const renderRoleEditor = (
    assignedRoles: RoleAssignment[],
    onChange: (roles: RoleAssignment[]) => void
  ) => (
    <div className="space-y-1">
      {roles.map((role) => {
        const assigned = assignedRoles.find((r) => r.role_id === role.id);
        return (
          <div key={role.id} className="flex items-center gap-2">
            <label className="flex items-center gap-1 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={!!assigned}
                onChange={() => onChange(toggleRole(assignedRoles, role.id))}
                className="rounded border-sage/30 bg-white/50"
              />
              {role.name}
            </label>
            {assigned && (
              <select
                value={assigned.skill_level}
                onChange={(e) =>
                  onChange(
                    updateSkillLevel(
                      assignedRoles,
                      role.id,
                      Number(e.target.value)
                    )
                  )
                }
                className="glass-input-sm text-xs"
              >
                {[1, 2, 3, 4, 5].map((l) => (
                  <option key={l} value={l}>
                    {t.employeesPage.skill} {l}
                  </option>
                ))}
              </select>
            )}
          </div>
        );
      })}
    </div>
  );

  const renderLocationEditor = (
    selectedIds: string[],
    onChange: (ids: string[]) => void
  ) => (
    <div className="space-y-1">
      {locations.map((loc) => (
        <label
          key={loc.id}
          className="flex items-center gap-1 text-sm text-gray-700 cursor-pointer"
        >
          <input
            type="checkbox"
            checked={selectedIds.includes(loc.id)}
            onChange={() => onChange(toggleId(selectedIds, loc.id))}
            className="rounded border-sage/30 bg-white/50"
          />
          {loc.name}
        </label>
      ))}
    </div>
  );

  const renderCompanyEditor = (
    selectedIds: string[],
    onChange: (ids: string[]) => void
  ) => (
    <div className="space-y-1">
      {companies.map((comp) => (
        <label
          key={comp.id}
          className="flex items-center gap-1 text-sm text-gray-700 cursor-pointer"
        >
          <input
            type="checkbox"
            checked={selectedIds.includes(comp.id)}
            onChange={() => onChange(toggleId(selectedIds, comp.id))}
            className="rounded border-sage/30 bg-white/50"
          />
          {comp.name}
        </label>
      ))}
    </div>
  );

  const renderRoleBadges = (empRoles: Employee["roles"]) => (
    <div className="flex flex-wrap gap-1">
      {empRoles.length === 0 && (
        <span className="text-gray-500 text-xs italic">{t.employeesPage.noRoles}</span>
      )}
      {empRoles.map((r) => (
        <span
          key={r.role_id}
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${badge.role}`}
        >
          {roleName(r.role_id)}
          <span className={badge.roleSkill}>L{r.skill_level}</span>
        </span>
      ))}
    </div>
  );

  const renderLocationBadges = (locIds: string[] | null) => (
    <div className="flex flex-wrap gap-1">
      {(!locIds || locIds.length === 0) && (
        <span className="text-gray-500 text-xs italic">{t.common.none}</span>
      )}
      {locIds?.map((id) => (
        <span
          key={id}
          className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${badge.location}`}
        >
          {locationName(id)}
        </span>
      ))}
    </div>
  );

  const renderCompanyBadges = (compIds: string[]) => (
    <div className="flex flex-wrap gap-1">
      {compIds.length === 0 && (
        <span className="text-gray-500 text-xs italic">{t.common.none}</span>
      )}
      {compIds.map((id) => (
        <span
          key={id}
          className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${badge.company}`}
        >
          {companyName(id)}
        </span>
      ))}
    </div>
  );

  const hasMultipleCompanies = companies.length > 1;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.employeesPage.title}</h1>
        <DemoGuard>
          <button
            onClick={() => setShowImportModal(true)}
            className="glass-btn-success"
          >
            {t.employeesPage.importData}
          </button>
        </DemoGuard>
      </div>
      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      {showImportModal && (
        <ImportModal
          title={t.employeesPage.importTitle}
          format={{
            csv: `full_name,email,role_names,skill_levels,location_names\nJane Doe,jane@example.com,Barista|Cashier,3|2,Downtown|Uptown\nJohn Smith,john@example.com,Manager,5,Downtown`,
            json: `[\n  {\n    "full_name": "Jane Doe",\n    "email": "jane@example.com",\n    "role_names": "Barista|Cashier",\n    "skill_levels": "3|2",\n    "location_names": "Downtown|Uptown"\n  },\n  {\n    "full_name": "John Smith",\n    "email": "john@example.com",\n    "role_names": "Manager",\n    "skill_levels": "5",\n    "location_names": "Downtown"\n  }\n]`,
          }}
          onUpload={handleImportUpload}
          onClose={() => setShowImportModal(false)}
        />
      )}
      <div className="glass-card overflow-x-auto">
        <table className={`min-w-full divide-y ${border.divider}`}>
          <thead className={bg.tableHeader}>
            <tr>
              <th className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.employeesPage.fullName}
              </th>
              <th className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.email}
              </th>
              <th className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.locations}
              </th>
              {hasMultipleCompanies && (
                <th className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                  {t.common.companies}
                </th>
              )}
              <th className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.roles}
              </th>
              <th className={`px-4 py-3 text-right text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.actions}
              </th>
            </tr>
          </thead>
          <tbody className={`divide-y ${border.divider}`}>
            {employees.map((emp) => {
              const isEditing = editingId === emp.id;
              return (
                <tr key={emp.id} className={isEditing ? bg.editingRow : ""}>
                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        type="text"
                        className="glass-input-sm w-full"
                        value={editValues.full_name}
                        onChange={(e) =>
                          setEditValues((v) => ({
                            ...v,
                            full_name: e.target.value,
                          }))
                        }
                      />
                    ) : (
                      <span className={`text-sm ${text.body}`}>{emp.full_name}</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        type="text"
                        className="glass-input-sm w-full"
                        value={editValues.email}
                        onChange={(e) =>
                          setEditValues((v) => ({
                            ...v,
                            email: e.target.value,
                          }))
                        }
                      />
                    ) : (
                      <span className={`text-sm ${text.body}`}>{emp.email ?? ""}</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {isEditing
                      ? renderLocationEditor(editValues.location_ids, (ids) =>
                          setEditValues((v) => ({ ...v, location_ids: ids }))
                        )
                      : renderLocationBadges(emp.location_ids)}
                  </td>
                  {hasMultipleCompanies && (
                    <td className="px-4 py-2">
                      {isEditing
                        ? renderCompanyEditor(editValues.company_ids, (ids) =>
                            setEditValues((v) => ({ ...v, company_ids: ids }))
                          )
                        : renderCompanyBadges(emp.company_ids)}
                    </td>
                  )}
                  <td className="px-4 py-2">
                    {isEditing
                      ? renderRoleEditor(editValues.roles, (r) =>
                          setEditValues((v) => ({ ...v, roles: r }))
                        )
                      : renderRoleBadges(emp.roles)}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2">
                    {isEditing ? (
                      <>
                        <button
                          onClick={handleSave}
                          className={action.save}
                        >
                          {t.common.save}
                        </button>
                        <button
                          onClick={cancelEdit}
                          className={action.cancel}
                        >
                          {t.common.cancel}
                        </button>
                      </>
                    ) : (
                      <>
                        <DemoGuard>
                          <button
                            onClick={() => startEdit(emp)}
                            className={action.edit}
                          >
                            {t.common.edit}
                          </button>
                        </DemoGuard>
                        <DemoGuard>
                          <button
                            onClick={() => handleDelete(emp.id)}
                            className={action.delete}
                          >
                            {t.common.delete}
                          </button>
                        </DemoGuard>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}

            {/* Add new row */}
            {showAddRow && (
              <tr className={bg.addRow}>
                <td className="px-4 py-2">
                  <input
                    type="text"
                    className="glass-input-sm w-full"
                    placeholder={t.employeesPage.fullName}
                    value={addValues.full_name}
                    onChange={(e) =>
                      setAddValues((v) => ({
                        ...v,
                        full_name: e.target.value,
                      }))
                    }
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="text"
                    className="glass-input-sm w-full"
                    placeholder={t.common.email}
                    value={addValues.email}
                    onChange={(e) =>
                      setAddValues((v) => ({ ...v, email: e.target.value }))
                    }
                  />
                </td>
                <td className="px-4 py-2">
                  {renderLocationEditor(addValues.location_ids, (ids) =>
                    setAddValues((v) => ({ ...v, location_ids: ids }))
                  )}
                </td>
                {hasMultipleCompanies && (
                  <td className="px-4 py-2">
                    {renderCompanyEditor(addValues.company_ids, (ids) =>
                      setAddValues((v) => ({ ...v, company_ids: ids }))
                    )}
                  </td>
                )}
                <td className="px-4 py-2">
                  {renderRoleEditor(addValues.roles, (r) =>
                    setAddValues((v) => ({ ...v, roles: r }))
                  )}
                </td>
                <td className="px-4 py-2 text-right space-x-2">
                  <button
                    onClick={handleCreate}
                    className={action.save}
                  >
                    {t.common.add}
                  </button>
                  <button
                    onClick={() => {
                      setShowAddRow(false);
                      setAddValues({
                        full_name: "",
                        email: "",
                        roles: [],
                        location_ids: [],
                        company_ids: [],
                      });
                    }}
                    className={action.cancel}
                  >
                    {t.common.cancel}
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {!showAddRow && (
          <div className="mt-3 mb-3 ml-3">
            <DemoGuard>
              <button
                onClick={() => setShowAddRow(true)}
                className="glass-btn-primary"
              >
                {t.employeesPage.addRow}
              </button>
            </DemoGuard>
          </div>
        )}
      </div>
    </div>
  );
}
