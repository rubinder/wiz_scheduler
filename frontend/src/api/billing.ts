import { apiFetch } from "./client";

export interface AiCreditStatus {
  can_generate: boolean;
  free_remaining_usd: number;
  purchased_credits_usd: number;
  is_over_free_tier: boolean;
  monthly_cost_usd: number;
}

export interface ScheduleQuota {
  can_generate: boolean;
  schedules_used: number;
  schedules_free_tier: number;
  is_over_free_tier: boolean;
  purchased_credits_usd: number;
  next_block_cost_usd: number;
}

export function getAiCredits(): Promise<AiCreditStatus> {
  return apiFetch<AiCreditStatus>("/schedules/ai-credits");
}

export function getScheduleQuota(): Promise<ScheduleQuota> {
  return apiFetch<ScheduleQuota>("/schedules/schedule-quota");
}

export function purchaseCredits(
  amountUsd: number
): Promise<{ session_id: string; url: string }> {
  return apiFetch<{ session_id: string; url: string }>(
    "/billing/purchase-credits",
    {
      method: "POST",
      body: JSON.stringify({ amount_usd: amountUsd }),
    }
  );
}

export function confirmCredits(
  sessionId: string
): Promise<{ credits_added_usd: number; new_balance_usd: number }> {
  return apiFetch<{ credits_added_usd: number; new_balance_usd: number }>(
    "/billing/confirm-credits",
    {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    }
  );
}
