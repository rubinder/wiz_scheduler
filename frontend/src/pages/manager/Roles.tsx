import { useCallback, useEffect, useState } from "react";
import * as rolesApi from "../../api/roles";
import DataTable, { type Column } from "../../components/shared/DataTable";
import type { Role } from "../../types";

const columns: Column[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "description", label: "Description", type: "text" },
  { key: "external_id", label: "External ID", type: "readonly" },
];

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState("");

  const fetchRoles = useCallback(async () => {
    try {
      setRoles(await rolesApi.listRoles());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load roles");
    }
  }, []);

  useEffect(() => {
    fetchRoles();
  }, [fetchRoles]);

  const handleSave = async (idx: number, row: Record<string, unknown>) => {
    try {
      await rolesApi.updateRole(roles[idx].id, {
        name: row.name as string,
        description: (row.description as string) || null,
      });
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async (idx: number) => {
    try {
      await rolesApi.deleteRole(roles[idx].id);
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleCreate = async (row: Record<string, unknown>) => {
    try {
      await rolesApi.createRole({
        name: row.name as string,
        description: (row.description as string) || null,
      });
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Roles</h1>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      <div className="bg-white rounded-lg shadow">
        <DataTable
          columns={columns}
          data={roles as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          onCreate={handleCreate}
        />
      </div>
    </div>
  );
}
