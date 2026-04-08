import { useCallback, useEffect, useState } from "react";
import * as rolesApi from "../../api/roles";
import DataTable, { type Column } from "../../components/shared/DataTable";
import ImportModal from "../../components/shared/ImportModal";
import DemoGuard from "../../components/shared/DemoGuard";
import type { Role } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

const columns: Column[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "description", label: "Description", type: "text" },
  { key: "external_id", label: "External ID", type: "readonly" },
];

export default function Roles() {
  const { t } = useLanguage();
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState("");
  const [showImportModal, setShowImportModal] = useState(false);

  const fetchRoles = useCallback(async () => {
    try {
      const data = await rolesApi.listRoles();
      setRoles(data.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" })));
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
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.rolesPage.title}</h1>
        <DemoGuard>
          <button
            onClick={() => setShowImportModal(true)}
            className="glass-btn-success"
          >
            {t.rolesPage.importData}
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
          title={t.rolesPage.importTitle}
          format={{
            csv: `name,description\nBarista,Prepares coffee and espresso drinks\nCashier,Handles register and payments\nShift Lead,Supervises team during shifts`,
            json: `[\n  {\n    "name": "Barista",\n    "description": "Prepares coffee and espresso drinks"\n  },\n  {\n    "name": "Cashier",\n    "description": "Handles register and payments"\n  },\n  {\n    "name": "Shift Lead",\n    "description": "Supervises team during shifts"\n  }\n]`,
          }}
          onUpload={handleImportUpload}
          onClose={() => setShowImportModal(false)}
        />
      )}
      <div className="glass-card">
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
