import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as billingApi from "../../api/billing";
import type { PayrollArchiveStatus } from "../../api/billing";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import { formatArchivePrice } from "../../utils/payrollArchive";

/**
 * The paid seven-year payroll archive (#218), shown to admins of a canceled
 * group next to the free full-history download, which stays available.
 * Renders nothing unless the server says the offer is available or already
 * bought. Also completes the Checkout return (payroll_archive_session_id).
 */
export default function PayrollArchiveOffer() {
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState<PayrollArchiveStatus | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    const sessionId = searchParams.get("payroll_archive_session_id");
    if (sessionId) {
      // Strip it first so a reload never retries the confirmation.
      searchParams.delete("payroll_archive_session_id");
      setSearchParams(searchParams, { replace: true });
    }
    const load = sessionId
      ? billingApi.confirmPayrollArchive(sessionId).catch((err) => {
          if (!cancelled) setError(t.payrollArchive.confirmFailed);
          console.error("Payroll archive confirmation failed", err);
          return billingApi.getPayrollArchiveStatus();
        })
      : billingApi.getPayrollArchiveStatus();
    load
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isAdmin]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!isAdmin || !status || (!status.available && !status.purchased)) {
    return error ? <div className="glass-alert-error mt-4">{error}</div> : null;
  }

  const deleteDate = status.scheduled_deletion_at
    ? new Date(status.scheduled_deletion_at).toLocaleDateString()
    : "";

  const buy = async () => {
    setError("");
    setRedirecting(true);
    try {
      const { url } = await billingApi.payrollArchiveCheckout();
      window.location.href = url;
    } catch {
      setRedirecting(false);
      setError(t.payrollArchive.checkoutFailed);
    }
  };

  return (
    <div className="glass-card p-4 mt-4 max-w-2xl" data-testid="payroll-archive-offer">
      {error && <div className="glass-alert-error mb-3">{error}</div>}
      {status.purchased ? (
        <p className={`text-sm ${text.body}`}>
          {t.payrollArchive.purchased.replace("{date}", deleteDate)}
        </p>
      ) : (
        <>
          <p className={`font-semibold ${text.body}`}>
            {t.payrollArchive.offerTitle.replace("{price}", formatArchivePrice(status.price_usd))}
          </p>
          <p className={`text-sm mt-1 mb-3 ${text.muted}`}>
            {t.payrollArchive.offerBody.replace("{date}", deleteDate)}
          </p>
          <button
            type="button"
            className="glass-btn-primary"
            disabled={redirecting}
            onClick={() => void buy()}
          >
            {redirecting ? t.payrollArchive.redirecting : t.payrollArchive.buy}
          </button>
        </>
      )}
    </div>
  );
}
