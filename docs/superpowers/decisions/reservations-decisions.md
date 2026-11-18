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

## Before switching reservations on (owner checklist)

- Step 5 (subscription, spend caps, per-company opt-in) must ship first;
  otherwise every active location gets a public chat on the operator's bill.
- Set `PUBLIC_ORIGIN_SECRET` and the matching CloudFront origin header
  (decision 28), or restrict the load balancer to CloudFront (decision 25).
- Legal: privacy policy / DPA wording for diner data (spec section 11).
- Set each location's staff transfer number.
- Run the evaluation suite (step 4) live once and record the baseline.
- Stripe webhook endpoint: add `customer.subscription.created` (the AI host
  subscription's row can be created from it; the other agent events,
  `checkout.session.completed`, `customer.subscription.updated/deleted`,
  `invoice.payment_failed/succeeded` and `invoice.upcoming`, are already
  subscribed for scheduling).
- Once the endpoint is subscribed to `customer.subscription.created`, add it
  to `stripe_reconcile.REQUIRED_WEBHOOK_EVENTS` (adding it before would raise
  a reconcile alert every run).
- Customer Portal: do not allow switching prices across products (the AI
  host and scheduling subscriptions share the customer; a switch would move
  one subscription onto the other's price).
- The frontend billing page at `/manager/billing` (the agent Checkout's
  return URL) must exist before `RESERVATIONS_ENABLED` goes on.
