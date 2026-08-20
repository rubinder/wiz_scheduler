import { useCallback, useEffect, useState } from "react";

import { getPlan } from "../api/billing";
import type { PlanState } from "../types";

/**
 * Plan state for the current ownership group.
 *
 * Drives proactive UI limits. This is UX only — the API is the security
 * boundary, so every consumer must still handle a 402 from the server:
 * counts can go stale when a second manager on the same group adds a row.
 */
export function usePlan() {
  const [plan, setPlan] = useState<PlanState | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setPlan(await getPlan());
    } catch {
      // Fail open in the UI: the server still enforces the limit.
      setPlan(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { plan, loading, refresh };
}
