import { useCallback, useEffect, useMemo, useState } from "react";
import * as locationsApi from "../../api/locations";
import * as regionsApi from "../../api/regions";
import DataTable, { type Column } from "../../components/shared/DataTable";
import ImportModal from "../../components/shared/ImportModal";
import DemoGuard from "../../components/shared/DemoGuard";
import SignalWeightConfig from "../../components/shared/SignalWeightConfig";
import { usePlan } from "../../hooks/usePlan";
import type { Location, Region, SignalConfig } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

export default function Locations() {
  const { t } = useLanguage();
  const { plan, refresh: refreshPlan } = usePlan();
  const [locations, setLocations] = useState<Location[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [error, setError] = useState("");
  const [showImportModal, setShowImportModal] = useState(false);
  const [selectedLocationId, setSelectedLocationId] = useState<string | null>(null);
  const [editingSignalConfig, setEditingSignalConfig] = useState<SignalConfig | null>(null);

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

  const overtimeGated = plan?.plan === "free";

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
      ...(overtimeGated
        ? []
        : [
            {
              key: "overtime_threshold_hours",
              label: "Overtime threshold (h/wk)",
              type: "number" as const,
              placeholder: "e.g. 40",
              title:
                "Hours/week after which the scheduler avoids assigning more hours to an " +
                "employee at this location. Leave blank to use the company default (or 40h).",
            },
            {
              key: "overtime_premium_multiplier",
              label: "Overtime premium multiplier",
              type: "number" as const,
              placeholder: "e.g. 1.5",
              title:
                "Pay multiplier for overtime hours at this location. Leave blank to use the " +
                "company default (or 1.5x).",
            },
          ]),
    ],
    [regions, overtimeGated]
  );

  // Blank/invalid → null (no constraint); otherwise a non-negative number.
  const parseMinRest = (val: unknown): number | null => {
    if (val === null || val === undefined || String(val).trim() === "") return null;
    const n = Number(val);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };

  const handleSave = async (idx: number, row: Record<string, unknown>) => {
    try {
      const location = locations[idx];
      await locationsApi.updateLocation(location.id, {
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: row.timezone as string,
        min_rest_hours: parseMinRest(row.min_rest_hours),
        overtime_threshold_hours: overtimeGated ? undefined : parseMinRest(row.overtime_threshold_hours),
        overtime_premium_multiplier: overtimeGated ? undefined : parseMinRest(row.overtime_premium_multiplier),
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
        overtime_threshold_hours: parseMinRest(row.overtime_threshold_hours),
        overtime_premium_multiplier: parseMinRest(row.overtime_premium_multiplier),
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
      {overtimeGated && (
        <p className={`text-xs ${text.muted} mb-2`}>
          Upgrade to a paid plan to configure overtime settings for a location.
        </p>
      )}
      <div className="glass-card">
        <DataTable
          columns={columns}
          data={locations as unknown as Record<string, unknown>[]}
          onSave={handleSave}
          onDelete={handleDelete}
          onCreate={handleCreate}
          createDisabled={locationLimitReached}
          createDisabledReason={
            locationLimitReached
              ? t.locationsPage.limitReached.replace(
                  "{limit}",
                  String(plan?.locations.limit)
                )
              : undefined
          }
        />
        {!overtimeGated && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <div className="flex flex-wrap gap-2">
              {locations.map((loc) => (
                <button
                  key={loc.id}
                  onClick={() => {
                    setSelectedLocationId(loc.id);
                    setEditingSignalConfig(loc.signal_config || {
                      seniority_weight: 0,
                      pay_weight: 0,
                      overtime_weight: 0,
                      affinity_weight: 0,
                    });
                  }}
                  className="text-sm glass-btn-secondary"
                >
                  ⚙️ {loc.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {selectedLocationId && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="glass-card p-6 max-w-lg w-full max-h-screen overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className={`text-lg font-bold ${text.heading}`}>
                Signal Weight Settings
              </h2>
              <button
                onClick={() => {
                  setSelectedLocationId(null);
                  setEditingSignalConfig(null);
                }}
                className="text-gray-500 hover:text-gray-700"
              >
                ✕
              </button>
            </div>

            {editingSignalConfig !== null && (
              <SignalWeightConfig
                config={editingSignalConfig}
                onChange={setEditingSignalConfig}
                readOnly={false}
              />
            )}

            <div className="flex gap-2 mt-6">
              <button
                onClick={() => {
                  setSelectedLocationId(null);
                  setEditingSignalConfig(null);
                }}
                className="flex-1 glass-btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  if (selectedLocationId && editingSignalConfig) {
                    const location = locations.find(
                      (l) => l.id === selectedLocationId
                    );
                    if (location) {
                      try {
                        await locationsApi.updateLocation(selectedLocationId, {
                          name: location.name,
                          region_id: location.region_id,
                          address: location.address,
                          timezone: location.timezone,
                          min_rest_hours: location.min_rest_hours,
                          signal_config: editingSignalConfig as unknown as Record<string, number>,
                        });
                        setSelectedLocationId(null);
                        setEditingSignalConfig(null);
                        await fetchData();
                      } catch (err: unknown) {
                        setError(
                          err instanceof Error
                            ? err.message
                            : "Update failed"
                        );
                      }
                    }
                  }
                }}
                className="flex-1 glass-btn-primary"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
