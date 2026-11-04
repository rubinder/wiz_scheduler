# Billing & Plans

The `OwnershipGroup` aggregate: subscription, plan limits, AI credits,
charges, overage metering, cancellation and data deletion.
`services/billing*.py`, `routers/billing.py` and `routers/webhooks.py` are
protected paths, so changes wait for the owner.

## Modules owned

- `backend/services/billing.py`: usage and cost (`check_and_record_usage`,
  `calculate_cost`), credits (`check_ai_credits`, `charge_saved_card`,
  `auto_reload_if_needed`, `deduct_credits_*`), metering
  (`compute_monthly_overage`, `bill_monthly_overages_for_og`,
  `count_schedules_this_month`, `utc_month_start`), lifecycle
  (`send_subscription_ended_email`, `delete_og_and_dependents`), and the
  `AutoReload*` errors. It is one module. The `services/billing*.py` glob
  has no siblings today.
- `backend/services/plan.py`: `get_plan_state`, `assert_can_add`,
  `assert_roster_editable`, `check_can_generate`, `assert_paid_plan`,
  `assert_payroll_history_access`.
- `backend/services/location_quota.py`, `operator_alerts.py`,
  `signup_signals.py` (`record_signup_signals`), `abuse_report.py`
  (`build_suspected_accounts_report`); `backend/scripts/run_abuse_report.py`.
- Routers: `billing`, `webhooks` (Stripe, signature-verified),
  `ownership_group`. Models: `ownership_group`, `billing_charge`,
  `token_usage`, `token_usage_daily`, `storage_snapshot`.
- Frontend: `api/billing.ts`, `api/ownershipGroup.ts`, `hooks/usePlan.ts`.

## Invariants

- `aggregate-root`: the plan is derived from `OwnershipGroup` on every read
  (`get_plan_state`) and never stored. Paid means an active Stripe
  subscription that is not cancelled.
- Every successful credit charge (pack purchase or auto-reload) goes through
  `charge_saved_card`, which fires the operator top-up alert. There is no
  second charge path. Stripe money calls happen only in `services/billing*`
  (`stripe-outside-billing`). The one allowlisted exception is the payroll
  archive refund (see `payroll.md`).
- AI spend debits purchased credits. `INCLUDED_LLM_USD` is 0 in production
  and is a demo knob only.
- `fail-closed`: gates raise before the side effect. `check_can_generate`
  gives 402 before any LLM call. `assert_can_add` takes a row lock on the
  group before counting. The webhook refuses a missing signature or an
  unconfigured secret. Post-generation billing never raises on a payment
  outcome, because the pre-generation gate is the consent point.
- `get_plan_state` returns unlimited when a company has no ownership group
  (seed and dev data). This is a deliberate exception. Do not copy it into a
  new gate.
- `signup_*` columns and the abuse report observe only. Nothing reads them to
  allow, deny, suspend or delete.

## Entry points for other contexts

`plan.assert_can_add`, `assert_paid_plan`, `check_can_generate`,
`assert_roster_editable`, `get_plan_state`; `billing.check_ai_credits`,
`check_and_record_usage`, `get_ownership_group_id`, `utc_month_start`,
`delete_og_and_dependents` (only the day-90 deletion job).

## Do not

- Store a plan, tier or "is_paid" flag anywhere.
- Call `stripe.PaymentIntent.create`, `Refund.create`, `Charge.create` or
  `Invoice.pay` outside `services/billing*.py`.
- Add a credit charge that bypasses `charge_saved_card`.
- Raise from post-generation billing.
- Enforce anything on `signup_*` or on the abuse report's clusters.
- Count only active rows for the schedule quota, or delete this month's
  schedule rows. Both hand the quota back.

## Tests that pin it

`tests/test_billing_integration.py`, `test_plan_integration.py`,
`test_plan_enforcement_integration.py`, `test_plan_generation_gate_integration.py`,
`test_plan_billing_api_integration.py`, `test_plan_demo_exception_integration.py`,
`test_quota_matches_plan_integration.py`, `test_location_quota_integration.py`,
`test_operator_alerts_integration.py`, `test_signup_signals_integration.py`,
`test_abuse_report_integration.py`, `test_architecture.py`.
