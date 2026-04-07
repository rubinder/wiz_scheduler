import { useCallback, useEffect, useState } from "react";
import * as regionsApi from "../../api/regions";
import DataTable, { type Column } from "../../components/shared/DataTable";
import type { Region } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";

const columns: Column[] = [
  { key: "name", label: "Name", type: "text" },
];

export default function Regions() {
  const { t } = useLanguage();
  const [regions, setRegions] = useState<Region[]>([]);
  const [error, setError] = useState("");

  const fetchRegions = useCallback(async () => {
    try {
      setRegions(await regionsApi.listRegions());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load regions");
    }
  }, []);

  useEffect(() => {
    fetchRegions();
  }, [fetchRegions]);

  const handleSave = async (idx: number, row: Record<string, unknown>) => {
    try {
      await regionsApi.updateRegion(regions[idx].id, {
        name: row.name as string,
      });
      await fetchRegions();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async (idx: number) => {
    try {
      await regionsApi.deleteRegion(regions[idx].id);
      await fetchRegions();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleCreate = async (row: Record<string, unknown>) => {
    try {
      await regionsApi.createRegion({ name: row.name as string });
      await fetchRegions();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">{t.regions.title}</h1>
      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      <div className="glass-card">
        <DataTable
          columns={columns}
          data={regions as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          onCreate={handleCreate}
        />
      </div>
    </div>
  );
}
