import { describe, expect, it } from "vitest";
import {
  agentStatusKey,
  canSubscribe,
  checkoutReturn,
  formatDollars,
  isComped,
  periodEndLabel,
  priceValues,
  survivesCancellationUntil,
  usageAmounts,
} from "./agentBilling";

describe("agent subscription status", () => {
  it("maps every Stripe status the card names to its own label key", () => {
    for (const s of ["active", "trialing", "past_due", "canceled", "paused", "unpaid", "incomplete"]) {
      expect(agentStatusKey(s)).toBe(s);
    }
  });

  it("labels a comped group Included, never offers Subscribe or a renewal date, and hides its usage bill", () => {
    expect(agentStatusKey("comped")).toBe("comped");
    expect(canSubscribe("comped")).toBe(false);
    expect(isComped("comped")).toBe(true);
    expect(isComped("active")).toBe(false);
    expect(isComped(null)).toBe(false);
    expect(
      periodEndLabel({ status: "comped", current_period_end: "2026-12-18T00:00:00+00:00", cancel_at_period_end: false }),
    ).toBeNull();
  });

  it("treats no subscription as none, an expired incomplete one as canceled, anything else as unknown", () => {
    expect(agentStatusKey(null)).toBe("none");
    expect(agentStatusKey("incomplete_expired")).toBe("canceled");
    expect(agentStatusKey("something_new")).toBe("unknown");
  });

  it("offers Subscribe unless a subscription is live (past_due still answers, Ruling T)", () => {
    expect(canSubscribe(null)).toBe(true);
    expect(canSubscribe("canceled")).toBe(true);
    expect(canSubscribe("incomplete_expired")).toBe(true);
    expect(canSubscribe("incomplete")).toBe(true);
    expect(canSubscribe("unpaid")).toBe(true);
    expect(canSubscribe("paused")).toBe(true);
    expect(canSubscribe("active")).toBe(false);
    expect(canSubscribe("trialing")).toBe(false);
    expect(canSubscribe("past_due")).toBe(false);
  });
});

describe("usage formatting", () => {
  it("formats dollars with cents", () => {
    expect(formatDollars(5, "en")).toBe("$5.00");
    expect(formatDollars(12.3, "en")).toBe("$12.30");
    expect(formatDollars(0, "en")).toBe("$0.00");
  });

  it("formats billed, included and overage, flagging overage only when there is some", () => {
    expect(usageAmounts({ billed_usd: 7.25, included_usd: 5, overage_usd: 2.25 }, "en")).toEqual({
      billed: "$7.25",
      included: "$5.00",
      overage: "$2.25",
      hasOverage: true,
    });
    expect(usageAmounts({ billed_usd: 3, included_usd: 5, overage_usd: 0 }, "en").hasOverage).toBe(false);
  });
});

describe("renews or cancels on", () => {
  const base = { status: "active", current_period_end: "2026-12-18T00:00:00+00:00", cancel_at_period_end: false };

  it("says renews for a live subscription and cancels when set to cancel at period end", () => {
    expect(periodEndLabel(base)).toEqual({ key: "renewsOn", iso: base.current_period_end });
    expect(periodEndLabel({ ...base, cancel_at_period_end: true })).toEqual({
      key: "cancelsOn",
      iso: base.current_period_end,
    });
    expect(periodEndLabel({ ...base, status: "past_due" })?.key).toBe("renewsOn");
  });

  it("says nothing without a live subscription or a period end", () => {
    expect(periodEndLabel({ ...base, status: "canceled" })).toBeNull();
    expect(periodEndLabel({ ...base, status: null })).toBeNull();
    expect(periodEndLabel({ ...base, current_period_end: null })).toBeNull();
  });
});

describe("AI host after the scheduling subscription is cancelled (decision 39)", () => {
  const cancelled = { canceled_at: "2026-11-01T00:00:00Z", scheduled_deletion_at: "2027-01-30T00:00:00Z" };

  it("warns with the deletion date while the AI host subscription is live", () => {
    expect(survivesCancellationUntil("active", cancelled)).toBe(cancelled.scheduled_deletion_at);
    expect(survivesCancellationUntil("past_due", cancelled)).toBe(cancelled.scheduled_deletion_at);
  });

  it("stays quiet without an AI host subscription or a scheduling cancellation", () => {
    expect(survivesCancellationUntil(null, cancelled)).toBeNull();
    expect(survivesCancellationUntil("canceled", cancelled)).toBeNull();
    expect(survivesCancellationUntil("active", { canceled_at: null, scheduled_deletion_at: null })).toBeNull();
    expect(survivesCancellationUntil("active", null)).toBeNull();
  });
});

describe("checkout return", () => {
  it("reads success with its session id, and cancel", () => {
    expect(checkoutReturn(new URLSearchParams("agent_checkout=success&session_id=cs_123"))).toEqual({
      kind: "success",
      sessionId: "cs_123",
    });
    expect(checkoutReturn(new URLSearchParams("agent_checkout=cancel"))).toEqual({ kind: "cancel" });
  });

  it("ignores anything else, including success without a session id", () => {
    expect(checkoutReturn(new URLSearchParams(""))).toBeNull();
    expect(checkoutReturn(new URLSearchParams("agent_checkout=success"))).toBeNull();
    expect(checkoutReturn(new URLSearchParams("agent_checkout=bogus&session_id=x"))).toBeNull();
  });
});

describe("price line values", () => {
  it("comes from the API: monthly price, included usage, markup as a percentage over cost", () => {
    expect(priceValues({ price_usd_monthly: 10, usage_markup: 1.5, usage: { included_usd: 5 } }, "en")).toEqual({
      price: "$10",
      included: "$5",
      markup: "50",
    });
  });

  it("keeps cents when an amount has them and rounds the markup percentage", () => {
    expect(priceValues({ price_usd_monthly: 12.5, usage_markup: 1.333, usage: { included_usd: 2.25 } }, "en")).toEqual({
      price: "$12.50",
      included: "$2.25",
      markup: "33",
    });
  });
});
