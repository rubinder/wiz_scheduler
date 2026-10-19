import { useCallback, useEffect, useState } from "react";
import * as rolesApi from "../../api/roles";
import DataTable, { type Column } from "../../components/shared/DataTable";
import ImportModal from "../../components/shared/ImportModal";
import DemoGuard from "../../components/shared/DemoGuard";
import type { Role } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import { isArchived, visibleRows } from "../../utils/archived";

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
  const [showArchived, setShowArchived] = useState(false);

  const fetchRoles = useCallback(async () => {
    try {
      const data = await rolesApi.listRoles(true);
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
      await rolesApi.updateRole(shownRoles[idx].id, {
        name: row.name as string,
        description: (row.description as string) || null,
      });
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  // DataTable indexes into the array it is given, so handlers index into the
  // same filtered array.
  const shownRoles = visibleRows(roles, showArchived);

  const handleDelete = async (idx: number) => {
    if (!window.confirm(t.rolesPage.archiveConfirm)) return;
    try {
      await rolesApi.deleteRole(shownRoles[idx].id);
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Archive failed");
    }
  };

  const handleRestore = async (idx: number) => {
    try {
      await rolesApi.restoreRole(shownRoles[idx].id);
      await fetchRoles();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Restore failed");
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
        <div className="flex items-center gap-3">
          <label className={`flex items-center gap-2 text-sm ${text.muted}`}>
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            {t.common.showArchived}
          </label>
          <DemoGuard>
            <button
              onClick={() => setShowImportModal(true)}
              className="glass-btn-success"
            >
              {t.rolesPage.importData}
            </button>
          </DemoGuard>
        </div>
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
          data={shownRoles as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          deleteLabel={t.common.archive}
          isArchived={(i) => isArchived(shownRoles[i])}
          onRestore={handleRestore}
          onCreate={handleCreate}
        />
      </div>
    </div>
  );
}
