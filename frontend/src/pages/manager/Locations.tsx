import { useCallback, useEffect, useMemo, useState } from "react";
import * as locationsApi from "../../api/locations";
import * as regionsApi from "../../api/regions";
import DataTable, { type Column } from "../../components/shared/DataTable";
import ImportModal from "../../components/shared/ImportModal";
import DemoGuard from "../../components/shared/DemoGuard";
import { usePlan } from "../../hooks/usePlan";
import type { Location, Region } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

export default function Locations() {
  const { t } = useLanguage();
  const { plan, refresh: refreshPlan } = usePlan();
  const [locations, setLocations] = useState<Location[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [error, setError] = useState("");
  const [showImportModal, setShowImportModal] = useState(false);

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

  // Proactive UX-only check — the API is the security boundary and is
  // always re-checked server-side (a concurrent add by another manager
  // on the same ownership group can make this stale).
  const locationLimitReached =
    plan?.plan === "free" &&
    plan.locations.limit !== null &&
    plan.locations.count >= plan.locations.limit;

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
      {
        key: "min_rest_hours",
        label: "Min rest (h)",
        type: "number",
        placeholder: "e.g. 11",
        title:
          "Minimum hours of rest between an employee's shifts on different days. " +
          "Set 11 for NYC Fair Workweek compliance (no clopenings). Leave blank for no limit.",
      },
    ],
    [regions]
  );

  // Blank/invalid → null (no constraint); otherwise a non-negative number.
  const parseMinRest = (val: unknown): number | null => {
    if (val === null || val === undefined || String(val).trim() === "") return null;
    const n = Number(val);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };

  const handleSave = async (idx: number, row: Record<string, unknown>) => {
    try {
      await locationsApi.updateLocation(locations[idx].id, {
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: row.timezone as string,
        min_rest_hours: parseMinRest(row.min_rest_hours),
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
      await refreshPlan();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  // Count CSV data rows (excluding header and blank lines) so we can
  // refuse client-side before uploading a file the server would reject
  // whole. This is a UX nicety only — it is skipped for JSON uploads and
  // is never authoritative; the server still enforces the real limit.
  const countCsvRows = async (file: File): Promise<number> => {
    const text = await file.text();
    return text
      .split("\n")
      .slice(1) // header
      .filter((line) => line.trim().length > 0).length;
  };

  const handleImportUpload = async (file: File) => {
    if (
      file.name.toLowerCase().endsWith(".csv") &&
      plan?.plan === "free" &&
      plan.locations.limit !== null
    ) {
      const rows = await countCsvRows(file);
      const remaining = plan.locations.limit - plan.locations.count;
      if (rows > remaining) {
        throw new Error(
          t.locationsPage.csvLimitError
            .replace("{rows}", String(rows))
            .replace("{remaining}", String(remaining))
        );
      }
    }
    const result = await locationsApi.bulkUploadLocations(file);
    await fetchData();
    await refreshPlan();
    return result;
  };

  const handleCreate = async (row: Record<string, unknown>) => {
    try {
      await locationsApi.createLocation({
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: (row.timezone as string) || "UTC",
        min_rest_hours: parseMinRest(row.min_rest_hours),
      });
      await fetchData();
      await refreshPlan();
    } catch (err: unknown) {
      // Also surfaces the server's 402 plan_limit_exceeded message inline:
      // ApiError.message is already normalized from `detail.message` (see
      // api/client.ts), so this same path covers stale-count rejections
      // from a concurrent manager on the same ownership group.
      setError(err instanceof Error ? err.message : "Create failed");
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.locationsPage.title}</h1>
        <DemoGuard>
          <button
            onClick={() => setShowImportModal(true)}
            className="glass-btn-success"
          >
            {t.locationsPage.importData}
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
          title={t.locationsPage.importTitle}
          format={{
            csv: `name,region_name,address,timezone\nDowntown Branch,East Region,123 Main St,America/New_York\nUptown Branch,West Region,456 Oak Ave,America/Los_Angeles`,
            json: `[\n  {\n    "name": "Downtown Branch",\n    "region_name": "East Region",\n    "address": "123 Main St",\n    "timezone": "America/New_York"\n  },\n  {\n    "name": "Uptown Branch",\n    "region_name": "West Region",\n    "address": "456 Oak Ave",\n    "timezone": "America/Los_Angeles"\n  }\n]`,
          }}
          onUpload={handleImportUpload}
          onClose={() => setShowImportModal(false)}
        />
      )}
      <div className="glass-card">
        {locationLimitReached && (
          <div className="glass-alert-info m-4">
            {t.locationsPage.limitReached.replace(
              "{limit}",
              String(plan?.locations.limit)
            )}
          </div>
        )}
        <DataTable
          columns={columns}
          data={locations as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          onCreate={locationLimitReached ? undefined : handleCreate}
        />
      </div>
    </div>
  );
}
