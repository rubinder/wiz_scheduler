import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as billingApi from "../api/billing";
import { useAuth } from "./useAuth";

/**
 * Completes a payroll-archive Checkout return (#218) wherever the payroll
 * page lands, whichever view it then renders (history-only, paid, or an
 * error state): `payroll_archive_session_id` is stripped first, so a reload
 * never retries, then confirmed. `refreshKey` changes once the confirmation
 * settles so the offer reloads its status; `failed` is true when it failed.
 */
export function usePayrollArchiveReturn(): { refreshKey: number; failed: boolean } {
  const { isAdmin } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [refreshKey, setRefreshKey] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const sessionId = searchParams.get("payroll_archive_session_id");
    if (!sessionId || !isAdmin) return;
    searchParams.delete("payroll_archive_session_id");
    setSearchParams(searchParams, { replace: true });
    let cancelled = false;
    billingApi
      .confirmPayrollArchive(sessionId)
      .catch((err) => {
        console.error("Payroll archive confirmation failed", err);
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setRefreshKey((k) => k + 1);
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps

  return { refreshKey, failed };
}
