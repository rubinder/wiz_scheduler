import { useState } from "react";

import { upgradeCheckout } from "../../api/billing";
import { useLanguage } from "../../i18n/LanguageContext";
import type { PlanState } from "../../types";

interface PlanBannerProps {
  plan: PlanState;
}

type ReasonKey =
  | "reasonPlanLimitExceeded"
  | "reasonSubscriptionCanceled"
  | "reasonAiRequiresPaid"
  | "reasonScheduleLimitReached";

const REASON_KEYS: Record<string, ReasonKey> = {
  plan_limit_exceeded: "reasonPlanLimitExceeded",
  subscription_canceled: "reasonSubscriptionCanceled",
  ai_requires_paid_plan: "reasonAiRequiresPaid",
  schedule_limit_reached: "reasonScheduleLimitReached",
};

/**
 * Free-tier status banner shown above the Schedule page's generation
 * controls. Renders nothing on a paid plan. Explains why generation may
 * be blocked or AI-gated, shows current usage against the free limits,
 * and offers an upgrade CTA that hands off to Stripe Checkout.
 */
export default function PlanBanner({ plan }: PlanBannerProps) {
  const { t } = useLanguage();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (plan.plan === "paid") return null;

  const reasonKey = REASON_KEYS[plan.block_reason ?? ""] ?? "reasonAiRequiresPaid";
  const message = t.planBanner[reasonKey];

  const employeeLimit =
    plan.employees.limit != null ? String(plan.employees.limit) : "∞";
  const locationLimit =
    plan.locations.limit != null ? String(plan.locations.limit) : "∞";
  let usageText = t.planBanner.usage
    .replace("{employeeCount}", String(plan.employees.count))
    .replace("{employeeLimit}", employeeLimit)
    .replace("{locationCount}", String(plan.locations.count))
    .replace("{locationLimit}", locationLimit);

  // `limit` is null for paid (unlimited) generations — only free groups
  // have a monthly cap, so only they get the third usage segment.
  if (plan.schedules.limit !== null) {
    usageText += t.planBanner.usageGenerations
      .replace("{generationCount}", String(plan.schedules.count))
      .replace("{generationLimit}", String(plan.schedules.limit));
  }

  const handleUpgrade = async () => {
    setLoading(true);
    setError("");
    try {
      const { url } = await upgradeCheckout();
      window.location.href = url;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t.planBanner.upgradeFailed);
      setLoading(false);
    }
  };

  return (
    <div className="glass-alert-warning mb-4">
      <p className="mb-2">{message}</p>
      <p className="mb-3 text-xs opacity-80">{usageText}</p>
      {error && <p className="mb-2 text-xs text-red-700">{error}</p>}
      <button
        onClick={handleUpgrade}
        disabled={loading}
        className="glass-btn-primary text-xs px-3 py-1"
      >
        {loading ? t.planBanner.upgrading : t.planBanner.upgradeButton}
      </button>
    </div>
  );
}
