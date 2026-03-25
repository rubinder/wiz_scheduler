import { useCallback, useEffect, useState } from "react";
import * as rolesApi from "../../api/roles";
import DataTable, { type Column } from "../../components/shared/DataTable";
import ImportModal from "../../components/shared/ImportModal";
import type { Role } from "../../types";

const columns: Column[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "description", label: "Description", type: "text" },
  { key: "external_id", label: "External ID", type: "readonly" },
];

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState("");
  const [showImportModal, setShowImportModal] = useState(false);

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

  const handleImportUpload = async (file: File) => {
    const result = await rolesApi.bulkUploadRoles(file);
    await fetchRoles();
    return result;
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
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Roles</h1>
        <button
          onClick={() => setShowImportModal(true)}
          className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
        >
          Import Data
        </button>
      </div>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      {showImportModal && (
        <ImportModal
          title="Import Roles"
          format={{
            csv: `name,description\nBarista,Prepares coffee and espresso drinks\nCashier,Handles register and payments\nShift Lead,Supervises team during shifts`,
            json: `[\n  {\n    "name": "Barista",\n    "description": "Prepares coffee and espresso drinks"\n  },\n  {\n    "name": "Cashier",\n    "description": "Handles register and payments"\n  },\n  {\n    "name": "Shift Lead",\n    "description": "Supervises team during shifts"\n  }\n]`,
          }}
          onUpload={handleImportUpload}
          onClose={() => setShowImportModal(false)}
        />
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
