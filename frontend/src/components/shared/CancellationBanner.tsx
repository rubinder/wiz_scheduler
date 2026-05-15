import { useEffect, useState } from "react";
import * as billingApi from "../../api/billing";
import type { BillingUsage } from "../../api/billing";
import { useLanguage } from "../../i18n/LanguageContext";

/**
 * Sitewide red banner that renders when the current manager's OG is in
 * the read-only grace period. Polls /billing/usage once on mount and
 * once every 60s while visible. Shows "Subscription ended on X. Data
 * deletion on Y. [Reactivate]".
 */
export default function CancellationBanner() {
  const { t } = useLanguage();
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [reactivating, setReactivating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchUsage = () => {
      billingApi
        .getUsage()
        .then((u) => {
          if (!cancelled) setUsage(u);
        })
        .catch(() => {});
    };
    fetchUsage();
    const id = setInterval(fetchUsage, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (!usage?.is_read_only) return null;

  const handleReactivate = async () => {
    setReactivating(true);
    try {
      const { url } = await billingApi.reactivateCheckout();
      window.location.href = url;
    } catch {
      setReactivating(false);
    }
  };

  const endDate = usage.canceled_at ? new Date(usage.canceled_at).toLocaleDateString() : "";
  const deleteDate = usage.scheduled_deletion_at
    ? new Date(usage.scheduled_deletion_at).toLocaleDateString()
    : "";

  return (
    <div className="bg-red-600 text-white text-sm px-4 py-2 flex items-center justify-between flex-wrap gap-2">
      <div>
        <strong>{t.cancellationBanner.title}</strong>{" "}
        {t.cancellationBanner.body
          .replace("{endDate}", endDate)
          .replace("{deleteDate}", deleteDate)}
      </div>
      <button
        onClick={handleReactivate}
        disabled={reactivating}
        className="bg-white text-red-700 px-3 py-1 rounded text-xs font-semibold hover:bg-red-50 disabled:opacity-50"
      >
        {reactivating ? t.schedule.redirectingToPayment : t.schedule.reactivateSubscription}
      </button>
    </div>
  );
}
