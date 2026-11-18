/**
 * Display helpers for the AI host billing card. The server decides whether
 * the AI host answers (`answering`); these only choose labels.
 */
import type { AgentUsage } from "../api/billing";

export type AgentStatusKey =
  | "none"
  | "active"
  | "trialing"
  | "past_due"
  | "canceled"
  | "paused"
  | "unpaid"
  | "incomplete"
  | "unknown";

const NAMED = new Set(["active", "trialing", "past_due", "canceled", "paused", "unpaid", "incomplete"]);

/** Subscriptions Stripe still bills and that block a second Checkout (past_due included, Ruling T). */
const LIVE = new Set(["active", "trialing", "past_due"]);

/** The i18n key under reservations.billing.statuses for a Stripe status. */
export function agentStatusKey(status: string | null): AgentStatusKey {
  if (status === null) return "none";
  if (status === "incomplete_expired") return "canceled";
  return NAMED.has(status) ? (status as AgentStatusKey) : "unknown";
}

/** Subscribe is offered unless a subscription is live (the server cancels leftovers first). */
export function canSubscribe(status: string | null): boolean {
  return status === null || !LIVE.has(status);
}

/** Whole-cent dollar amounts (the API rounds them); utils/conversations.formatUsd is for sub-cent costs. */
export function formatDollars(amount: number, locale: string): string {
  return new Intl.NumberFormat(locale, { style: "currency", currency: "USD" }).format(amount);
}

export function usageAmounts(
  usage: Pick<AgentUsage, "billed_usd" | "included_usd" | "overage_usd">,
  locale: string,
): { billed: string; included: string; overage: string; hasOverage: boolean } {
  return {
    billed: formatDollars(usage.billed_usd, locale),
    included: formatDollars(usage.included_usd, locale),
    overage: formatDollars(usage.overage_usd, locale),
    hasOverage: usage.overage_usd > 0,
  };
}

/** "Renews on" or "Cancels on" the period end, for a live subscription only. */
export function periodEndLabel(s: {
  status: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}): { key: "renewsOn" | "cancelsOn"; iso: string } | null {
  if (s.status === null || !LIVE.has(s.status) || !s.current_period_end) return null;
  return { key: s.cancel_at_period_end ? "cancelsOn" : "renewsOn", iso: s.current_period_end };
}

/**
 * Decision 39: the AI host subscription outlives a cancelled scheduling
 * subscription until the account is deleted. Returns the deletion date to
 * warn with, or null when there is nothing to warn about.
 */
export function survivesCancellationUntil(
  agentStatus: string | null,
  scheduling: { canceled_at?: string | null; scheduled_deletion_at?: string | null } | null,
): string | null {
  if (agentStatus === null || !LIVE.has(agentStatus)) return null;
  if (!scheduling?.canceled_at || !scheduling.scheduled_deletion_at) return null;
  return scheduling.scheduled_deletion_at;
}

/** The AI host Checkout's return (backend BILLING_PAGE_PATH query), or null. */
export function checkoutReturn(
  params: URLSearchParams,
): { kind: "success"; sessionId: string } | { kind: "cancel" } | null {
  const flag = params.get("agent_checkout");
  if (flag === "cancel") return { kind: "cancel" };
  const sessionId = params.get("session_id");
  if (flag === "success" && sessionId) return { kind: "success", sessionId };
  return null;
}
