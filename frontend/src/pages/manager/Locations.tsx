import { useCallback, useEffect, useMemo, useState } from "react";
import * as locationsApi from "../../api/locations";
import * as regionsApi from "../../api/regions";
import DataTable, { type Column } from "../../components/shared/DataTable";
import type { Location, Region } from "../../types";

export default function Locations() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const [locs, regs] = await Promise.all([
        locationsApi.listLocations(),
        regionsApi.listRegions(),
      ]);
      setLocations(locs);
      setRegions(regs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const columns: Column[] = useMemo(
    () => [
      { key: "name", label: "Name", type: "text" },
      {
        key: "region_id",
        label: "Region",
        type: "select",
        options: regions.map((r) => ({ value: r.id, label: r.name })),
      },
      { key: "address", label: "Address", type: "text" },
      { key: "timezone", label: "Timezone", type: "text" },
    ],
    [regions]
  );

  const handleSave = async (idx: number, row: Record<string, unknown>) => {
    try {
      await locationsApi.updateLocation(locations[idx].id, {
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: row.timezone as string,
      });
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async (idx: number) => {
    try {
      await locationsApi.deleteLocation(locations[idx].id);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const handleCreate = async (row: Record<string, unknown>) => {
    try {
      await locationsApi.createLocation({
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: (row.timezone as string) || "UTC",
      });
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Locations</h1>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      <div className="bg-white rounded-lg shadow">
        <DataTable
          columns={columns}
          data={locations as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          onCreate={handleCreate}
        />
      </div>
    </div>
  );
}
