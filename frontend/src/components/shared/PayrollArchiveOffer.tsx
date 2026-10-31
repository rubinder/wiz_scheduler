import { useEffect, useState } from "react";
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
 * bought. The Checkout return is completed by usePayrollArchiveReturn on
 * the page, in every view; `refreshKey` reloads the status after it.
 */
export default function PayrollArchiveOffer({ refreshKey = 0 }: { refreshKey?: number }) {
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState<PayrollArchiveStatus | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    billingApi
      .getPayrollArchiveStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isAdmin, refreshKey]);

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
