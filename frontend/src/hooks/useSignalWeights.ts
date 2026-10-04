import { useState } from "react";
import { updateCompany } from "../api/company";
import { updateLocation } from "../api/locations";
import type { SignalConfig } from "../types";

interface UseSignalWeightsResult {
  updateCompanySignalConfig: (config: SignalConfig | null) => Promise<void>;
  updateLocationSignalConfig: (locationId: string, config: SignalConfig | null) => Promise<void>;
  loading: boolean;
  error: string | null;
}

/**
 * Hook for managing signal weight configurations at company and location level.
 */
export function useSignalWeights(): UseSignalWeightsResult {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateCompanySignalConfig = async (config: SignalConfig | null) => {
    try {
      setLoading(true);
      setError(null);
      await updateCompany({ signal_config: config as Record<string, number> | null });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update signal weights";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateLocationSignalConfig = async (locationId: string, config: SignalConfig | null) => {
    try {
      setLoading(true);
      setError(null);
      await updateLocation(locationId, { signal_config: config as Record<string, number> | null });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update location signal weights";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return {
    updateCompanySignalConfig,
    updateLocationSignalConfig,
    loading,
    error,
  };
}
