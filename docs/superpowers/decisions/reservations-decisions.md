# Reservations + AI host: decisions log

Decisions made while building without stopping to ask (owner instruction,
2026-11-18). Each: what, why, cost if wrong. Newest last.

1. **Churn is part of a booking's table range.** `reservation_tables.during`
   = dining start to dining end + the party band's churn. The database's
   no-overlap constraint therefore enforces churn too. Cost if wrong: a
   churn change in the rules doesn't move existing bookings.
2. **Capacity and pacing count dining time only**, not churn: diners leave
   when they finish; the reset only blocks the table.
3. **Staff bookings ignore the booking cutoff** (e.g. "no bookings within
   60 minutes"), so a host can book a walk-in for 15 minutes from now. The
   AI agent keeps the cutoff. Booking horizon still applies to everyone.
4. **Merging to main with the feature switch off.** Each step merges after
   green CI with `RESERVATIONS_ENABLED=false` in production, so new tables
   ship but nothing is visible. Billable AWS resources (Connect numbers,
   Lex bots) default to zero in Terraform until the owner switches them on.
5. **Tenant isolation for foreign keys is enforced in the services, not with
   composite foreign keys.** A security scan noted that FK checks bypass
   row-level security, so a crafted id could point at another tenant's
   table. Every service resolves ids from the payload with
   `company_id` + `location_id` filters and rejects unknown ones; tests
   cover it. This matches how the rest of the schema is built (simple FKs).
   Cost if wrong: a missed check in a future service could link rows across
   tenants; composite FKs would be the stronger backstop to add later.
6. **All availability math runs in UTC instants**, converting to the
   location zone only to read wall-clock rules and to present results.
   Review caught that Python adds durations to zoned datetimes by wall
   clock, which made meals an hour short on the spring-forward night and
   could double-book. Cost if wrong: none known; DST tests pin it.
7. **A hold with no expiry time blocks its table** (fail closed), per a
   security review: a missing value must never free capacity.
8. **Staff can't book start times in the past**, even with the cutoff
   bypass; a walk-in already at the table is recorded by seating it.
9. **Section cover limits count dining time only**, like restaurant
   capacity (decision 2).
10. **A diner can't grow a booking past the self-booking party limit** by
    modifying it; that would skip staff approval. Staff can.
11. **Lock order is location first.** Every write locks the location row,
    then touches reservations. Future code must keep that order to avoid
    deadlocks.
12. **Walk-ins have their own path** (corrects decision 8): staff can seat a
    walk-in now on a free table in one step, with no phone number needed.
    Past start times still can't be booked. A walk-in is not bound to the
    slot grid or service periods (the party is at the door); table fit,
    churn, capacity, pacing and section limits still apply.
13. **A location with upcoming reservations can't be archived** (409); the
    manager cancels or moves them first. Cost if wrong: one extra step for
    the manager, instead of diners arriving at a closed restaurant. The
    7shifts import does not 409 (its cooldown is already spent): it leaves
    such a location active and reports it in the import's `errors`.
14. **A table combination's minimum party is its largest member's
    minimum**, so two 2-tops pushed together can seat 3.
15. **A combination spanning two sections counts the whole party against
    each section's limit** (conservative).
16. **Pacing counts bookings starting within the slot's interval**, so
    changing the interval doesn't make old bookings invisible to pacing.
17. **Staff bookings don't need a phone number**; diner (agent) bookings do.
18. **The AI host can never change a booking by itself.** Booking needs the
    diner's Confirm button; cancelling and changing a booking also need a
    button press after the agent proposes it (security review).
19. **A diner gets 3 tries to look up a booking** by code + phone per
    conversation, then must call or start over.
20. **Large parties:** on web chat they become a request the restaurant
    approves; on the phone the agent transfers to staff.
21. **Each tool round commits on its own**, so one slow model reply never
    holds a restaurant's booking lock.
22. *(Superseded by 25 and 28.)* **Public chat rate limits key on the address our own proxies saw**
    (the X-Forwarded-For entry 2 hops from the right: CloudFront, then the
    load balancer), not the first entry, which a client can forge. The
    app's older shared helper still trusts the first entry for the login
    and forgot-password limits: **owner follow-up**, not changed overnight
    because it affects sign-in.
23. *(Superseded by 27.)* **Failed booking lookups lock out across chats:** 5 failures for one
    phone at one restaurant in 15 minutes blocks further tries, whichever
    chat they come from.
24. **One turn at a time per chat.** A second message sent while the agent
    is still answering gets "please wait"; the turn cap can't be raced.
25. **The load balancer accepts traffic that skips CloudFront** (security
    group open to the internet, no origin secret), so the chat limits check
    two keys: the viewer address CloudFront saw and the address that
    connected to the load balancer. **Owner follow-up:** restrict the load
    balancer to CloudFront's origin-facing prefix list (or an origin secret
    header); then one key suffices and the login limiters become safe too.
26. **No hard per-restaurant chat cap** (it would let one actor block a
    restaurant); per-(restaurant, client) limits plus a warning when a
    restaurant sees unusual volume.
27. **Lookup lockouts are per (restaurant, phone, client)**, so a stranger
    can't lock a real diner out; heavy abuse of one number sends diners to
    staff instead of refusing them.
28. **Origin secret for the public chat.** When `PUBLIC_ORIGIN_SECRET` is
    set, the public chat routes only answer requests carrying CloudFront's
    `X-Origin-Verify` header, and limits key on the viewer address alone.
    **Switch-on prerequisite (owner):** add that header to the CloudFront
    origin in Terraform with a secret value, and set the same secret in the
    ECS task. Until then the two-key limits apply.
29. **No raw IP addresses are stored.** Rate-limit and lockout keys are
    stored as a keyed hash; conversations are deleted after a year.
30. **Diners' data requests go through the restaurant.** Diners aren't
    WizScheduler users, so the restaurant (the data controller) handles
    their access and erasure requests with two manager tools: export and
    erase a diner's data by phone number at a location. A manager's own
    GDPR export stays about the manager. Retention removes diner contact
    details and transcripts after a year automatically.
31. **Managers see what the agent said and did, not its internal
    reasoning blocks**; stored raw copies stay server-side.
32. **Large-party requests only hold tables once submitted.** Until the
    diner presses Confirm, a large-party offer is held for the normal
    5 minutes; managers only see and approve submitted requests.
33. **Diners confirm changes against structured details**, not the model's
    wording: the chat shows the current and proposed booking above the
    Confirm button. A proposed cancellation can be dismissed ("Keep my
    booking").
34. **A turn never runs longer than 25 seconds** (under CloudFront's 30 s
    origin timeout); if it would, the diner is asked to send the message
    again and the chat stays open.
35. **The evaluation suite measures the voice channel's text only.** Voice
    audio runs through Lex (speech to text) and Polly (text to speech),
    which arrive with the phone channel in step 8. Until then the voice
    scenarios play the agent's voice prompt over text, and neither
    per-stage voice latency (recognition, model, synthesis) nor first-try
    understanding (how often the agent gets the caller's words right the
    first time) is measured. Step 8 adds both. Cost if wrong: a voice
    regression that lives in the audio path passes the suite.
36. **Ruling R: no eval tables in any real database.** The eval run and
    result models are metadata only (there is no migration); the runner
    creates them with the application's tables in its throwaway scratch
    schema, and writes every run (meta, per-attempt results, report) to
    `evals-runs/<run-id>.json` at the repo root (gitignored) before the
    schema is dropped. Comparing runs means comparing those files. The run
    forces `RLS_ENFORCE` off for its duration (isolation is the schema, not
    row-level security) and restores it after. Cost if wrong: run history
    lives on one developer's disk, not in a shared table.
37. **AI host overage is billed as reconciled InvoiceItems, not Metronome
    or Billing Meters.** Stripe recommends Metronome for new usage-based
    billing; that adds a vendor. Billing Meters is a lower-level primitive.
    The app already bills overage as reconciled InvoiceItems on the
    subscription's upcoming invoice, and the agent overage uses the same
    pattern: exact (we hold per-conversation billed amounts), no new vendor
    or dependency, and the invoice line reads "AI host usage over $5 —
    <period>". The AI host is a separate $10/month subscription
    (`services/billing_reservations.py`) with the first $5.00 of billed usage
    per period included, then cost x 1.5. Cost if wrong: moving to Meters or
    Metronome later replaces only the reconcile job.
38. **Ruling T: past_due keeps the AI host answering.** The agent answers
    while the subscription is `active`, `trialing` or `past_due` (Stripe is
    still retrying the card); `unpaid`, `paused`, `incomplete` and
    `canceled` stop it. The billing card labels past_due "Payment issue —
    Stripe is retrying". Cost if wrong: a restaurant whose card keeps
    failing gets AI host service until Stripe's retries run out.
39. **The AI host subscription lives and dies with the account.** It is
    separate from the scheduling subscription and keeps running (and
    billing) through the scheduling subscription's cancellation grace
    period; deleting the account (day 90) cancels it in Stripe first. The
    billing card (step 5, Task 4) must warn about this when the scheduling
    subscription is cancelled. Cost if wrong: a customer who cancels
    scheduling expects the AI host to stop too and is billed for it until
    the account is deleted.

40. **Voice is off by default, and phone numbers follow the subscription.**
    All Connect/Lex infrastructure is count-gated behind `voice_enabled`
    (and number claiming behind `voice_provisioning_enabled`). A number is
    claimed per location on subscribe and released 7 days after the
    subscription stops answering. Claims happen in checkout confirm (its
    own session) and the daily job, never in webhooks: the webhook path only
    records `requested`, which keeps it under Stripe's timeout. Why: a
    number costs money every day and a provider call can be slow. Cost if
    wrong: a webhook-only subscription gets its number up to a day late; a
    shorter grace period would drop a number a lapsed customer may want back.
41. **The bridge Lambda is zipped by CI and no new Terraform provider is
    added.** Steps the provider cannot do (building the Lex bot version and
    alias) run in `terraform/voice/publish_lex_bot.sh` between two applies
    (see `terraform/README.md`, voice section). Why: a provider swap or
    addition is a larger change than a script the owner runs once. Cost if
    wrong: a manual step on first switch-on and after a bot change.
42. **The bridge dedupes turns by sequence number inside the atomic claim,
    and a spoken "yes" books only through Lex confirmation.** The confirm
    intent maps to `confirm_*` and is checked against the last reply's
    `expect`, so a stray "yes" never books. Why: Lex and Lambda retry, and
    speech is ambiguous. Cost if wrong: a retried turn double-acts, or a
    caller must repeat a confirmation.
43. **Deviation: voice records no asr/tts/transport latency stages.** Lex V2
    gives the Lambda no timings, so only `llm` and `tools` are recorded.
    Follow-up: return `server_ms` in the reply so transport can be derived
    as round trip minus server time. Cost: less latency detail for voice.
44. **No `fallback_transfer_to` for now.** A backend failure (timeout or
    error) tells the caller to call back rather than transferring to the
    restaurant's staff line. Why: a transfer needs a number we must trust
    and test per location. Cost if wrong: a caller during an outage gets no
    human; the Lambda backend timeout is 6 s so the fallback is not slow.
45. **The daily reconcile schedule sits behind `agent_reconcile_enabled`**,
    turned on together with RESERVATIONS, since it also claims and releases
    numbers. Cost if wrong: if forgotten, overage is not re-synced and
    numbers are claimed only at checkout.

## Before switching reservations on (owner checklist)

- Step 5 (subscription, spend caps, per-company opt-in) must ship first;
  otherwise every active location gets a public chat on the operator's bill.
- Set `PUBLIC_ORIGIN_SECRET` and the matching CloudFront origin header
  (decision 28), or restrict the load balancer to CloudFront (decision 25).
- Legal: privacy policy / DPA wording for diner data (spec section 11).
- Set each location's staff transfer number.
- Run the evaluation suite (step 4) live once and record the baseline.
- Stripe: create the AI host product with a licensed $10/month recurring
  price and set `RESERVATIONS_PRICE_ID` to it (empty, the subscribe routes
  answer 503 `not_configured` and the billing card says billing is not
  available yet). Overage needs no Stripe price (decision 37).
- Stripe webhook endpoint: add `customer.subscription.created` (the AI host
  subscription's row can be created from it; the other agent events,
  `checkout.session.completed`, `customer.subscription.updated/deleted`,
  `invoice.payment_failed/succeeded` and `invoice.upcoming`, are already
  subscribed for scheduling).
- Stripe webhook endpoint: also add `invoice.voided` and
  `invoice.marked_uncollectible` (Ruling W: an AI host invoice that is voided
  or uncollectible marks its usage charges failed and alerts the operator;
  without them those charges stay pending).
- Once the endpoint is subscribed to `customer.subscription.created`,
  `invoice.voided` and `invoice.marked_uncollectible`, add them to
  `stripe_reconcile.REQUIRED_WEBHOOK_EVENTS` (adding them before would raise
  a reconcile alert every run).
- Schedule `python -m backend.scripts.run_agent_overage_reconcile` daily
  (step 6 infra): it re-syncs every agent subscription, bills overage, and
  sends the final invoice of one whose deletion webhook was missed.
- Customer Portal: do not allow switching prices across products (the AI
  host and scheduling subscriptions share the customer; a switch would move
  one subscription onto the other's price).
- The frontend billing page at `/manager/billing` (the agent Checkout's
  return URL, admins only) exists since step 5 Task 4; after the first live
  subscribe, check that the return confirms the subscription.
- Before charging real customers for the AI host, run one Stripe test-mode
  pass of the final-invoice path (cancel a subscription with overage):
  confirm Stripe refuses deleting an invoice item that is on a finalized
  invoice, `invoices.list` returns draft and void invoices, deleted objects
  answer `resource_missing`, and the code takes the expected branch.
  Include a customer whose card is only on the AI host subscription (no
  customer-level invoice default): the final invoice must be charged to
  that card (it is created with the cancelled subscription's
  `default_payment_method`).

### Voice channel (step 6)

Setup steps are in the voice section of `terraform/README.md`.

- Check that Amazon Connect and Lex V2 are available in the deployment
  region.
- Set `voice_enabled=true` and apply. The bridge secret is seeded
  automatically.
- Run `terraform/voice/publish_lex_bot.sh`, set `voice_lex_bot_alias_arn`,
  and apply again.
- Open the contact flow once in the Connect console.
- Set `voice_provisioning_enabled`.
- Make one live test call.
- Turn on `agent_reconcile_enabled` together with RESERVATIONS (decision 45).
- Run a live test of `find_owned` in `services/connect_numbers.py`
  (`ListPhoneNumbersV2` with `PhoneNumberPrefix`); it was only tested
  against stubs.
- If the Lex build rejects the `AMAZON.FreeFormInput` slot, drop it.
- If a number Claim is denied, widen the IAM policy.
- Expected costs: a Connect DID daily fee per number, plus per-minute
  inbound, Lex speech requests, Polly neural characters, and the $50 budget
  alarm. See AWS pricing for current rates.
