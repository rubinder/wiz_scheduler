import { apiFetch } from "./client";

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
  schedules_free_tier: number;
  is_over_free_tier: boolean;
  purchased_credits_usd: number;
  next_block_cost_usd: number;
  autoreload_failed?: boolean;
}

export interface AutoReloadStatus {
  enabled: boolean;
  threshold_usd: number;
  amount_usd: number;
  current_balance_usd: number;
  failed_at: string | null;
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
