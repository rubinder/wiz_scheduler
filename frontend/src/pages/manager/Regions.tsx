import { useCallback, useEffect, useState } from "react";
import * as regionsApi from "../../api/regions";
import DataTable, { type Column } from "../../components/shared/DataTable";
import type { Region } from "../../types";

const columns: Column[] = [
  { key: "name", label: "Name", type: "text" },
];

export default function Regions() {
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
      <h1 className="text-2xl font-bold mb-6">Regions</h1>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      <div className="bg-white rounded-lg shadow">
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
