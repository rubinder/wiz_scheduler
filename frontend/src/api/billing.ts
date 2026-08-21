import { apiFetch } from "./client";
import type { PlanState } from "../types";

export interface AiCreditStatus {
  can_generate: boolean;
  free_remaining_usd: number;
  purchased_credits_usd: number;
  is_over_free_tier: boolean;
  monthly_cost_usd: number;
  autoreload_failed?: boolean;
}

export interface ScheduleQuota {
  can_generate: boolean;
  schedules_used: number;
  /**
   * The generation allowance that actually governs this tenant: the metered
   * SCHEDULE_FREE_TIER for paid groups, the free-plan cap for free ones. Kept
   * equal to PlanState.schedules.limit for free groups so this strip and the
   * plan banner cannot show different numbers.
   */
  schedules_free_tier: number;
  is_over_free_tier: boolean;
  purchased_credits_usd: number;
  next_block_cost_usd: number;
  autoreload_failed?: boolean;
  /** Free groups upgrade; only paid groups can buy overage credits. */
  plan?: "free" | "paid";
}

export interface AutoReloadStatus {
  enabled: boolean;
  threshold_usd: number;
  amount_usd: number;
  current_balance_usd: number;
  failed_at: string | null;
}

export interface PendingInvoiceItem {
  kind: "invoice_item_storage" | "invoice_item_employees";
  amount_usd: number;
  period: string;
}

export interface BillingUsage {
  base: { monthly_usd: number };
  llm: {
    input_tokens: number;
    output_tokens: number;
    raw_cost_usd: number;
    charged_usd: number;
    free_tier_usd: number;
    free_remaining_usd: number;
    is_over_free_tier: boolean;
    overage_markup: number;
  };
  storage: {
    used_gb: number;
    free_gb: number;
    billable_gb: number;
    cost_per_gb: number;
    charged_usd: number;
  };
  employees: {
    count: number;
    free_tier: number;
    billable: number;
    block_size: number;
    cost_per_block: number;
    charged_usd: number;
  };
  schedules: {
    count: number;
    free_tier: number;
    billable: number;
    block_size: number;
    cost_per_block: number;
    charged_usd: number;
  };
  total_monthly_charge_usd: number;
  pending_invoice_items: PendingInvoiceItem[];
  is_read_only?: boolean;
  canceled_at?: string | null;
  scheduled_deletion_at?: string | null;
}

export interface BillingChargeRow {
  id: string;
  kind: "autoreload" | "invoice_item_storage" | "invoice_item_employees";
  amount_usd: number;
  stripe_object_id: string | null;
  period: string | null;
  status: "succeeded" | "failed" | "pending";
  error_message: string | null;
  created_at: string;
}

export function getAiCredits(): Promise<AiCreditStatus> {
  return apiFetch<AiCreditStatus>("/schedules/ai-credits");
}

export function getScheduleQuota(): Promise<ScheduleQuota> {
  return apiFetch<ScheduleQuota>("/schedules/schedule-quota");
}

export function getAutoReload(): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload");
}

export function updateAutoReload(
  body: Partial<{ enabled: boolean; threshold_usd: number; amount_usd: number }>
): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function retryAutoReload(): Promise<AutoReloadStatus> {
  return apiFetch<AutoReloadStatus>("/billing/autoreload/retry", {
    method: "POST",
  });
}

export function getBillingCharges(limit = 50): Promise<{ charges: BillingChargeRow[] }> {
  return apiFetch<{ charges: BillingChargeRow[] }>(`/billing/charges?limit=${limit}`);
}

export function getPortalLink(): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/billing/portal-link");
}

export function getUsage(): Promise<BillingUsage> {
  return apiFetch<BillingUsage>("/billing/usage");
}

export function reactivateCheckout(): Promise<{ session_id: string; url: string }> {
  return apiFetch<{ session_id: string; url: string }>("/billing/reactivate-checkout", {
    method: "POST",
  });
}

export function confirmReactivation(
  sessionId: string
): Promise<{ reactivated: boolean; subscription_id: string }> {
  return apiFetch<{ reactivated: boolean; subscription_id: string }>(
    "/billing/confirm-reactivation",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }
  );
}

export function getPlan(): Promise<PlanState> {
  return apiFetch<PlanState>("/billing/plan");
}

export function upgradeCheckout(): Promise<{ session_id: string; url: string }> {
  return apiFetch<{ session_id: string; url: string }>("/billing/upgrade-checkout", {
    method: "POST",
  });
}

export function confirmUpgrade(sessionId: string): Promise<{ upgraded: boolean }> {
  return apiFetch<{ upgraded: boolean }>("/billing/confirm-upgrade", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}
