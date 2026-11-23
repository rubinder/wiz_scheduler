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
    *Closed by decision 56: step 8 measures both.*
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

46. **Screening precedence: an allow at either scope beats any block; then
    FTC; then answer.** Withheld (no caller ID) callers are always
    answered. A company-wide allow added later overrides an existing
    location block, by design. Why: a manager's explicit allow must never be
    silently overridden, and a caller we cannot identify cannot be judged.
    Cost if wrong: a withheld spammer reaches the agent and is billed as
    usage.
47. **Screening fails open.** The backend has 1 s to decide, the Lambda 3 s;
    any error, timeout or cold start answers the call. Why: a missed
    real booking costs more than one spam call. Cost if wrong: during an
    outage spam gets through.
48. **Blocked calls are not usage; answered calls are.** A blocked call never
    reaches the agent, so it consumes nothing. Cost if wrong: none for the
    owner; the manager just sees it in screening stats instead of usage.
49. **FTC threshold: 3 reports in 90 days.** Reports are stored per file
    date, so a re-run replaces that day's counts instead of adding to them.
    Dates in the window that are not `ok` are retried; missing files are
    retried only within 10 days. Cost if wrong: too low blocks real
    callers, too high lets spam through; it is a constant to tune.
50. **Spam tables are global and `app_role` is read-only on them**; the
    ingest writes as the owner role. Why: the data is not tenant data and
    no request path should be able to alter it. Cost if wrong: none beyond
    needing the owner connection for the ingest task.
51. **Number rules: one active entry per scope and number**, enforced by an
    advisory lock plus a partial unique index on permanent rules. Mark-spam
    and a location block are refused with 409 when an allow would override
    them. Cost if wrong: a manager must remove the allow first.
52. **Diner erase touches only that location's rules.** Company-wide rules
    appear in the export and are removed by admins from Number lists.
    Why: a diner's request to one restaurant must not edit another scope.
    Cost if wrong: a company-wide rule on the diner's number outlives the
    erase until an admin removes it.
53. **Ingest and screening are gated** by `ftc_ingest_enabled` and
    `voice_enabled`. Cost if wrong: forgetting the first leaves the FTC list
    empty, so only manager rules block.
54. **The FTC CSV URL is a setting** (`FTC_DNC_CSV_URL`). The FTC site refused
    a scripted fetch during planning (404), so it is verified live (see
    checklist). Cost if wrong: the ingest alerts and FTC blocking is off.
55. **`ix_spam_numbers_e164` was dropped** (it duplicated the primary key's
    leading column and slowed ingest writes). Cost if wrong: none, the PK
    serves reads.
56. **Voice evals measure the audio path (closes decision 35).** Three
    recognizers: `exact` (CI), `noisy`, `lex` (Polly 8 kHz into Lex V2
    `RecognizeUtterance`, live only). Cost if wrong: see 57 to 60.
57. **The noisy recognizer is deterministic and seeded** (default 259) with
    telephone-style confusions, so a measured change comes from the agent
    and not from the dice. Only `--live` runs measure understanding with it;
    replay ignores model input and refuses a recording made with a
    different recognizer or seed. Cost if wrong: confusions are a model of
    phone audio, not the real thing; lex is the check.
58. **First-try understanding is detected structurally**: the share of text
    turns not followed by a "say that again", a not-caught reply, or a
    re-elicit of the slot just supplied. No model call, so it is free and
    repeatable. Cost if wrong: an unusual phrasing of a re-ask is missed.
59. **Voice replay coverage is two hand-authored recordings**,
    `party_of_14_voice` (transfer) and `book_four_friday_7pm_voice`
    (confirm), so CI covers the bridge path without live spend. Cost if
    wrong: they do not follow live model behaviour until re-recorded.
60. **Voice knobs default to byte-identical behaviour.** `voice_reply_max_sentences`,
    `voice_read_back`, `voice_confirm_style` live in `RulesSchema.voice`,
    are API-only, and are recorded per run in `meta.voice_knobs`. A rules
    PUT (including a UI save) that omits `voice` keeps the stored values.
    Cost if wrong: a save would silently reset tuned values.
61. **The live tuning round is the owner's to run.** Each configuration
    costs about $3 of Claude for the 12 scenarios x 1 repeat (upper bound
    per `cost_estimate`) plus cents of Polly/Lex. Procedure: see
    `docs/reservations-agent-evals.md`. Results table (owner to fill):

    | Config | Recognizer | Pass rate | First-try understanding | Turn p95 (ms) | Cost per booking |
    | --- | --- | --- | --- | --- | --- |
    | baseline | noisy | | | | |
    | reply_max_sentences=2 | noisy | | | | |
    | read_back=true | noisy | | | | |
    | confirm_style=statement | noisy | | | | |
    | (same four) | lex | | | | |

### Booking providers (step 9)

62. **The provider seam.** The AI host agent books only through
    `BookingProvider` (`backend/reservations/providers/`); staff and manager
    routes stay WizScheduler-native and call `booking.*` directly. Why: the
    agent can then run against a system that is not ours without touching the
    staff path. Cost if wrong: an agent path that bypasses the seam books
    against the wrong inventory.
63. **The provider is fixed per conversation at creation**
    (`agent_conversations.provider`). Why: a rules change mid-call must not
    move a held slot to another system. Cost if wrong: a CLAUDE.md wording
    change.
64. **The mock owns its inventory.** `mock_external_*` tables have their own
    ids and codes; the mock never touches `reservations` or tables and never
    calls `find_slots`, so the single-evaluator rule covers WizScheduler
    inventory only. Cost if wrong: the mock would stop proving the seam.
65. **Conversation ref columns are provider-scoped.** Migration 0065 dropped
    the 4 foreign keys from conversations to `reservations`. Reservations are
    hard-deleted only after conversations; any future reservation-delete path
    must null the refs itself. Why: a ref may point into the mock's tables.
    Cost if wrong: a dangling ref in a deleted reservation's conversation.
66. **Idempotency keys.** Format `{conv}:{action}:{seq}`; primary key
    `(company_id, key)`; kept 24 h; hold releases send no key. Why: a retry
    after a timeout must not book twice. Cost if wrong: a duplicate booking,
    or a retry that replays a stale result after 24 h.
67. **Transient handling.** A transient provider error is retried once with
    the same key; a second one replies `RETRY_TEXT` plus the handoff with
    reason `provider_unavailable`. The mock injects failures before writing,
    seeded by `(conv, seq, action)`; evals use `FAILURE_SEED`. Cost if wrong:
    a diner hears a retry prompt instead of a person.
68. **External bookings in the host view** are read-only and carry no tables.
    Erase deletes mock bookings, whereas WizScheduler bookings are scrubbed
    and kept. Retention scrubs mock contact details, mirroring 7e. Cost if
    wrong: mock data outliving an erase request.
69. **Provider selection is admin-only.** `rules.provider` and
    `rules.mock_external` change only for an admin (403 `admin_only`). Cost
    if wrong: a regular manager could point a live location at the mock.

### Voice usage billing

70. **Voice calls bill their minutes and speech through the AI host usage.**
    Each phone turn that reaches the core adds
    `voice_cost(lex_requests, polly_chars, call_seconds)`
    (`reservations/agent/voice_cost.py`) to the cost of the first event that
    turn recorded, so it flows into `agent_conversations.cost_usd`, the
    "billed as it is spent" meter, `agent_usage` (cost x
    `RESERVATIONS_USAGE_MARKUP`, overage past the included amount) and the
    daily spend cap exactly like model cost. Formula, from the `VOICE_COST_*`
    settings (AWS us-east-1 list prices): `lex_requests x 0.004` (one per
    turn: the Lambda forwards keypad digits as the transcript too, so DTMF is
    not told apart and is counted as speech) `+ polly_chars x 16 / 1e6`
    (plain characters of the reply text and confirm question the Lambda
    speaks, at most 1000; SSML tags are not billed) `+ call_seconds x
    (0.018 + 0.0022) / 60`. Call seconds are an estimate: the database's
    `now()` at the end of the turn less the newest event of the previous
    turn (the call's start on its first turn), computed in SQL so both
    sides are the database clock, per second, clamped to 0 ..
    `VOICE_COST_MAX_SECONDS_PER_TURN` (120); no client-reported duration is
    trusted. Adding it runs in a savepoint and never raises into the call
    path: on a failure the turn is logged and goes unbilled, and the caller
    still hears the reply. A retried turn (same seq) is answered from the
    stored reply and not billed again; a conversation erased mid-turn keeps
    its call cost, as it keeps model cost. Polly is billed on the assumption
    that Connect charges it separately when it speaks Lex replies; confirm
    that before switch-on, and if Connect does not, set
    `VOICE_COST_POLLY_NEURAL_PER_MILLION_CHARS_USD=0`. Evals: `total_cost_usd`
    (and so cost per booking) stays model cost only, comparable with the
    step-8 baseline, because call seconds there measure the harness; the
    call cost is reported apart as `voice_cost_usd`. Not billed: the greeting turn's speech and the time after the
    last turn until hangup (no turn follows to charge them), turns that
    record no event (spend-cap closes, replays), staff transfer outbound
    minutes (AWS bills them; bounded by `VOICE_TRANSFERS_PER_DAY` and the $50
    voice budget alarm) and the per-number daily fee (covered by the $10
    base price). No schema change: the breakdown is logged
    (`reservation_agent.voice_cost`, no number or words), not stored. Cost if
    wrong: stale prices or the estimate under- or over-bill by fractions of a
    cent per turn (a 3-minute, 6-turn call is about $0.09 of AWS cost before
    markup); a too-low cap under-bills long silences, a too-high one
    over-bills a turn that waited on a stuck retry.

### Analytics (step 10)

71. **The lake is anonymous.** Parquet rows carry no diner names, phones,
    emails, confirmation codes, no `diner_key`/`source_key`/caller keys, and
    no turn text (`text_chars` only); tool payloads pass a key allowlist
    (`redact.py`). This tightens the plan's wording, which kept HMAC keys: an
    HMAC of a person is still personal data, and no metric needs diner
    identity. Why: erasure then needs no lake rewrite. Cost if wrong: no
    repeat-caller or transcript analytics without a new redaction pipeline.
72. **Partitions are idempotent.** The nightly job replaces one
    `<table>/dt=YYYY-MM-DD` partition and that day's `agent_daily_metrics`
    rows per UTC day, under a per-day advisory lock, and runs D-1 and D-2 so
    a conversation open at midnight gets its final state. Cost if wrong: a
    rerun would double count.
73. **Eval failures are stored as categories.** `eval_results` keeps the
    leading words of a failure, not its text, because the text can quote
    persona names. Eval results land only from local runs (where
    `evals-runs/` exists; it is gitignored, so it is absent from the image),
    never from ECS. Cost if wrong: failure detail
    needs the run files.
74. **Billed reconciliation is internal.** The quality check compares
    `agent_usage` with the day's conversations. Stripe is compared in the
    step 5 reconcile job, not here. Cost if wrong: a Stripe mismatch is
    caught by step 5 a day later than a lake check would.
75. **Partition projection instead of crawlers.** Glue tables project `dt`
    from 2026-11-01 to now, so new days need no crawler or `MSCK REPAIR`.
    Cost if wrong: a table moved to another start date needs a Terraform
    change.
76. **The app never queries Athena.** Insights reads `agent_daily_metrics`
    in Postgres only; Athena is the owner's ad hoc tool. Why: no AWS query
    cost or latency on a request path. Cost if wrong: a dashboard needing a
    lake-only column must first land it in Postgres.
77. **The lake bucket is not `prevent_destroy`.** With `count` it would fail
    the plan whenever `analytics_enabled` is off, as the non-empty bucket
    fails the apply. So turning the flag off with data in the bucket blocks
    the apply until the bucket is emptied. Cost if wrong: either way a
    blocked apply, never silent data loss.
78. **Analyst access is an MFA-only role.** `wizscheduler-analytics-reader` is
    a role (CI can create roles, not customer-managed policies), assumable
    only with MFA, with Athena on its workgroup and Glue writes limited to
    the views in `analytics/sql`. Cost if wrong: widen the role.
79. **Insights KPIs show a dash for partial windows.** A 7-day window that
    reaches before the fetched range is null, not a partial sum that would
    read as a real comparison. Cost if wrong: an early window looks empty.
80. **`covers_by_service_period` in Athena uses UTC hour.** The lake holds no
    time zone or service periods. The named-period figure (local time) is
    `agent_daily_metrics.covers_by_period` in Postgres. Covers count every
    booking source, not only agent bookings. Cost if wrong: add a source
    filter or move the view's bucketing into Postgres.

### Launch (step 11)

81. **`reservations_enabled` is the one Terraform switch, default false, and
    flipping it is plan-neutral until then.** It only sets
    `RESERVATIONS_ENABLED` in the ECS task; off, the task definition is
    byte-identical to before, so merging the launch branch changes nothing in
    production. It is flipped with `agent_reconcile_enabled` and only after
    legal approves. Cost if wrong: flipping early puts a public chat on the
    operator's bill with the privacy wording unapproved.

82. **The demo group is `comped`, a subscription status, not a coupon.** The
    demo ownership group has no Stripe customer, so there is nothing to put a
    coupon on, and a fake customer would be a path to real charges. A comped
    group is answered like an active one (`ANSWERING_STATUSES`), its usage is
    recorded for the analytics, and it is never invoiced; Stripe never sees
    it. The seed leaves a real Stripe subscription alone. Cost if wrong: a
    comped group's usage is visible but unbilled, so the operator absorbs it,
    which decision 83 bounds.

83. **A comped group's daily spend never exceeds
    `COMPED_DAILY_CAP_USD` ($3).** `subscription.daily_cap` takes the lower of
    that and the rules' cap. This came from an automated security review: the
    demo manager can raise `daily_spend_cap_usd` in the rules, and comped usage
    is never invoiced, so the rules cap alone is not a ceiling. The demo's own
    rules cap is $3 too, and a rules save above the ceiling for a comped group
    is refused with 422 `comped_cap_limit` (`subscription.check_rules_cap`;
    the detail carries `ceiling_usd`, and the rules editor shows the amount in
    all 19 locales). The rules GET returns a comped group's cap as
    min(stored, ceiling) (`subscription.rules_as_shown`), so the $20 default
    reads back as $3 and saving the rules unchanged is never refused. "Today" survives erasure (decision 93). The billing card's
    price line for a comped group says "Complimentary plan". Cost if wrong:
    the demo stops answering after $3 a day, or a raised cap on the demo costs
    the operator real money.

84. **A comped group gets no phone number.** `voice_numbers` excludes comped
    groups from claiming one. The demo is web chat; a Connect number costs
    money every month and a comped group brings no revenue. Cost if wrong: the
    demo has no phone line until this is changed.

85. **The demo is Fernhollow Kitchen, and all of its data is fictional.**
    `python -m backend.seed_reservations` turns the demo tenant's first
    location into a restaurant: floor, rules, a holiday closure, a week of
    bookings and the comped subscription. Diner phones are 555-01xx
    (`+1212555` then `0100` to `0199`) and emails are `@example.com`, so no number or
    address can reach a real person. The seed is idempotent: rows have fixed
    ids, tables are matched by label so same-label tables an earlier run or a
    manager made are reused, and a rerun adds only the new days. Every seeded
    booking, the large-party requests included, has the `staff` source: no
    conversation is behind any of them, and analytics counts `web_agent` rows
    as AI host bookings. The requests are held as `web_agent` (a staff hold is
    never a request) and then written as `staff`; a rerun moves rows an
    earlier seed left as `web_agent` (`rsv` ids, no conversation). The seeded
    diners, notes and booking plan live in `reservations/demo_data.py`, read
    by the seed and by the demo purge (decision 94), and a rerun restores the
    seed's details on any of this week's seeded rows that differ. Cost if
    wrong: a rerun duplicates tables, a seeded phone dials someone, or the
    demo's insights credit the AI host with bookings it never made.

86. **The location rename shows in the scheduling demo too.** The seed sets
    the demo location's name to Fernhollow Kitchen, and that location is the
    one the scheduling demo uses. Cost if wrong: the scheduling demo shows a
    restaurant name; rename it back by hand.

87. **Diner data can be erased by confirmation code (spec section 11).**
    `POST /locations/{id}/diner-data/erase-by-code` scrubs that one
    reservation's name, phone, email and notes and deletes its conversations
    and any mock booking with the code. Unlike phone erase it does not touch
    the diner's other reservations, lookup failures or number rules, because
    a code names one booking, not a person. A code carried by both a
    WizScheduler reservation and a mock booking at the location answers 409
    `ambiguous_code`: erasing one by guess could miss the other, so the
    manager erases by phone instead. When a conversation is still mid-turn the
    mock booking is scrubbed, not deleted, so retrying the same code finds it
    and finishes. Neither erase lowers the location's spend today (decision
    93). Cost if wrong: a diner's request for one booking takes more than one
    call.

88. **Parked: phone erase with a mock in-flight chat leaves that chat.** The
    phone path still deletes the mock bookings, so a conversation mid-turn
    when it runs is left. This is safe because
    `RESERVATIONS_MOCK_PROVIDER_ALLOWED` is off in production: no production
    location can select the mock provider (422 `mock_provider_not_allowed`),
    so no production chat books through it. Cost if wrong: a mid-turn chat on
    a non-production mock survives a phone erase.

89. **Each erase writes one audit line, with no code and no phone.**
    `diner_data.erase location=... user=... by=code|phone` plus the counts.
    The line must not carry what was erased. Cost if wrong: the audit trail
    itself holds the personal data it records the removal of.

90. **The host tells the diner it is automated, on every channel.** The spoken
    flow greeting says "an automated reservations assistant"; the web default
    greeting says "automated reservations assistant"; a custom greeting is
    refused on save unless it contains "automated", case-insensitive
    (`rules.discloses_automation`). "Assistant" alone no longer passes: a
    person can be an assistant. Stored greetings that predate the check are
    not rewritten, but one that fails it is never rendered (the default
    greeting is). The check matches English words only, so a greeting in
    another language that discloses it is refused: issue #285. Cost if wrong:
    a non-English restaurant must add one English word until #285 lands.

91. **Phone numbers stay out of Connect flow logs.** The contact flow's first
    block is `UpdateFlowLoggingBehavior` set to Disabled, before the screen
    Lambda and the bot see the caller number. As a backstop for anything
    written before that block runs, the `/aws/connect/<alias>` log group is
    created with 1-day retention, CloudWatch's shortest. This is untested on
    live Connect, which documents no error branch for the block: watch the
    second voice apply. Cost if wrong: caller numbers sit in flow logs for a
    day, or the apply fails on the block.

92. **The legal text is held as draft PR #284 with 13 open questions, and no
    agent merges it.** Only the owner, after legal reviews, merges it, and it
    is step 1 of the switch-on order. Cost if wrong: the product goes live with
    unapproved privacy and DPA wording.

93. **Today's spend survives erasure.** `subscription.spend_today` is the
    location's conversations by start day plus the `agent_usage` rows whose
    conversation is gone (`conversation_id IS NULL`, the FK sets it on
    delete) by `created_at`, at that location. The demo manager login is
    shown to every visitor, so with conversations alone chat, book, erase by
    code, repeat would reset "today" and the comped ceiling would never fire.
    The usage row keeps its location and cost after the erase, and its
    `created_at` is the conversation's first metered turn, seconds after it
    started. Not computed from `agent_usage` alone: `meter` writes no row
    for a cost that bills to $0.00, a `blocked` conversation or a company
    with no ownership group, and a live row lags its conversation's cost. It
    applies to every group, not only comped ones. Cost if wrong: an erased
    chat that never billed a cent drops out of today (under half a cent),
    or a chat that crossed local midnight counts on its usage row's day.

94. **The public demo location warns visitors and forgets them daily.** It
    is `RESERVATIONS_DEMO_COMPANY_ID` / `RESERVATIONS_DEMO_LOCATION_ID`
    (default `comp0001` / `locn0001`, `backend/seed.py`'s ids; a test keeps
    them equal, and app code never imports the seed). The public chat config
    returns `demo: true` there only, and the chat page shows "This is a demo
    restaurant. Please use made-up details — the demo account can see what
    you type." in all 19 locales (the rest of the diner page stays English).
    The daily retention sweep (`run_data_retention`, step 7f,
    `diner_data.purge_demo_location`) runs the erasure scrub there on what is
    older than 24 hours, and only when that company's group is comped: the
    ids are env-overridable, so pointed at a group that is not comped it
    purges nothing, logs a warning (ids only) and sends the operator a
    `demo_purge_refused` alert. What it scrubs: reservations created before then lose their contact
    fields, conversations started before then are deleted, mock bookings
    made before then are deleted, and lookup failures go. A seeded booking is
    kept only while its name, phone, email and notes still equal
    `demo_data.seeded_contact` for its id exactly, so the demo still shows a
    full book; one a demo manager edited (a real name behind the seeded
    `@example.com` email, say) is scrubbed like a visitor's, and the next
    seed rerun restores it. It acts on that location of that company only,
    and on nothing when it does not exist.
    Cost if wrong: a visitor who ignores the banner has their details
    visible to other visitors for up to a day.

95. **The automated-assistant disclosure is a per-location setting, on by
    default (owner request, 2026-11-20).** `RulesSchema.disclose_automated`
    (default true) is admin-only, like `provider`. When it is off, the
    default greeting says "reservations line" in place of "automated
    reservations assistant", and a custom greeting need not say
    "automated". The system prompt then adds "never say or imply that you
    are a person; if asked, say you are an automated assistant". The line is
    added only when the setting is off, so the default prompt and the eval
    baselines stay byte-identical. The phone line follows the same setting:
    `/voice/screen` returns the location's greeting, the screen Lambda
    passes it on (or a disclosing fallback when it has none), and the
    contact flow speaks `$.Attributes.greeting`. When the Lambda fails, the
    flow's `greet-fallback` block sets the disclosing text. Why: the owner
    wants restaurants to choose. Cost if wrong: some laws (for example the
    EU AI Act, Art. 50, and California's bot-disclosure law) may require
    the disclosure. A restaurant that turns it off carries that, as terms
    12.F already says. The dynamic flow prompt has not run against a live
    Connect instance (voice is off).

96. **The 13 legal open questions were settled the most permissive way
    (owner, 2026-11-20)** and written into the DPA on draft PR #284. The
    two exceptions were where the permissive wording would have been
    untrue: analytics is called "pseudonymous", not "anonymous", and
    Anthropic is no longer said to keep nothing after a request. The PR
    stays a draft for the lawyer.
97. **Switch-on before Stripe and the origin secret (owner, 2026-11-21).**
    The owner approved PR #284 as their own lawyer and it merged
    (704fc517); `reservations_enabled` and `agent_reconcile_enabled` were
    then defaulted to true in `terraform/variables.tf` (CI does not read
    the gitignored tfvars), with
    `analytics_enabled` and voice left off. `RESERVATIONS_PRICE_ID` is not
    set yet, so subscribing answers 503 `not_configured` and no customer
    location gets a public chat; only the comped demo location does,
    bounded by `COMPED_DAILY_CAP_USD`. `PUBLIC_ORIGIN_SECRET` is not set, so
    the two-key rate limits apply (decision 28). Cost if wrong: the demo
    chat spends up to $3 a day of operator model cost.
98. **$5.00 of our cost is included each period, not $5.00 of billed usage
    (owner, 2026-11-21).** `RESERVATIONS_INCLUDED_COST_USD=5.00` replaces
    `RESERVATIONS_INCLUDED_USAGE_USD`; the included billed usage is that cost
    times the markup ($7.50 at 1.5, `subscription.included_billed`), and
    overage is billed usage beyond it. The invoice line reads "AI host usage
    over the included $5.00 of cost", the billing card "includes $5 of our
    cost, then our cost + 50%", and terms 2.4 section F says the same. The
    Stripe product is AI Host (`prod_Vfl8LhIYGCAjKS`), price
    `price_1UePcAB7IPUEjgxs4Ij6egNp` ($10/month licensed), set as
    `RESERVATIONS_PRICE_ID` through `var.reservations_price_id`. Estimate
    for customers: a 2-minute, 5-turn call costs about $0.075 (Connect and
    telephony $0.040, Lex $0.020, Polly $0.012, model about $0.003), so the
    included $5.00 covers about 65 such calls, or over 1,000 web chats.
    Cost if wrong: at the old rule a heavy month under-billed nothing; at
    this one the owner's margin at the included limit is about $3.50 a month
    before the number's daily fee.
99. **The billing card shows about how many 1- and 2-minute calls the
    included cost covers (owner, 2026-11-22).** `billing_reservations.estimated_included_calls`
    prices a typical call (1 minute and 3 turns, or 2 minutes and 5 turns; 150 spoken characters a
    reply, 6,000 cached / 800 new input / 200 output tokens a turn) with the
    same settings usage is billed with, divides the included cost by it and
    rounds down to a multiple of 5 (120 one-minute calls at $0.040, 65
    two-minute calls at $0.074, at today's prices).
    It is an estimate for customers, not a quota. Cost if wrong: real calls
    run longer or shorter and the number is off; it never caps anything.

100. **The call opens with "table or event", then date, time and party
    together; events go to staff (owner, 2026-11-22).** The default greeting
    now asks the first question itself: "Hi, you've reached {name}'s
    automated reservations assistant. Are you booking a table, or is this
    about an event or private party?" (disclosure off: "...{name}'s
    reservations line. ..."). One wording serves web and voice because
    `default_greeting` does not know the channel and the callers share it;
    "is this about" reads right in both. The screen Lambda's
    `FALLBACK_GREETING` and the contact flow's `greet-fallback` say the same
    disclosing text. Custom greetings are untouched; the prompt's "Call flow"
    block tells the host to find out table-or-event unless the diner already
    said. Order on both channels: (a) table or event; (b) for a table, date,
    approximate time and party size in ONE question, then `find_slots`;
    (c) name and phone only after a slot is chosen; never re-ask what the
    diner said. Events, private parties, buyouts, catering, set menus,
    anything the rules cannot express and anything over the self-book limit
    call `handoff_to_staff` with reason `event` before any details are
    collected (voice transfers to `staff_transfer_number`; web shows the
    restaurant's number; with no number the existing no-number path applies).
    The host never asks which location: each number and each web chat
    belongs to one location, and a company-wide router would need a
    multi-location number, which does not exist. A diner asking for another
    location is handed off with reason `other_location`. The prompt bytes
    changed, so the golden hashes in `test_voice_knobs.py` were updated on
    purpose; the recordings carry no prompt and stay valid. New scenario
    `event_request_voice` (hand-built recording, no live call). Cost if
    wrong: a diner who wanted a table and says "party" loosely may be sent to
    staff; the handoff is one call away from a booking, and the staff line
    is where an event belongs anyway.

## Before switching reservations on (owner checklist)

### Switch-on order

1. Legal approves the draft PR #284 (13 open questions), and the owner merges
   it. No agent merges it (decision 92).
2. Do Stripe, voice and screening per the existing checklist sections.
3. Set `reservations_enabled`, `agent_reconcile_enabled` and, if wanted,
   `analytics_enabled` to true in one PR. CI applies it. Voice is separate
   and takes two applies (terraform/README.md "Voice channel"): first
   `voice_enabled` true (instance, bot, Lambda, secret, budget), then
   `voice/publish_lex_bot.sh`, then a second apply with
   `voice_lex_bot_alias_arn` set, which creates the contact flow. Watch that
   second apply for the `UpdateFlowLoggingBehavior` block (decision 91): it
   is untested on live Connect. Only after it set
   `voice_provisioning_enabled`.
4. Run the demo seed as a one-off ECS task: `python -m backend.seed_reservations`
   (idempotent, safe to rerun).
5. Smoke test: open `/r/<demo slug>/<demo location>`, book, then cancel.
6. Test-call a voice-enabled location (the demo has no number, decision 84;
   the greeting is the shared contact flow's) and check the spoken greeting
   says it is an automated assistant (decision 90).

- Step 5 (subscription, spend caps, per-company opt-in) must ship first;
  otherwise every active location gets a public chat on the operator's bill.
- Set `PUBLIC_ORIGIN_SECRET` and the matching CloudFront origin header
  (decision 28), or restrict the load balancer to CloudFront (decision 25).
- Legal: privacy policy / DPA wording for diner data (spec section 11).
- Set each location's staff transfer number.
- Run the evaluation suite (step 4) live once and record the baseline.
- Run the voice tuning round (decisions 56 to 61, `docs/reservations-agent-evals.md`)
  and record the results table under decision 61.
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
  a reconcile alert every run). Done 2026-11-22: the live endpoint has all
  ten events and the reconcile requires them.
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
- Check the VOICE_COST_* prices against current AWS pricing for the Connect
  region (decision 70).
- Confirm whether Connect bills Polly separately when it speaks Lex replies.
  If it does not, set `VOICE_COST_POLLY_NEURAL_PER_MILLION_CHARS_USD=0`.
- Turn on `agent_reconcile_enabled` together with RESERVATIONS (decision 45).
- Run a live test of `find_owned` in `services/connect_numbers.py`
  (`ListPhoneNumbersV2` with `PhoneNumberPrefix`); it was only tested
  against stubs.
- If the Lex build rejects the `AMAZON.FreeFormInput` slot, drop it.
- If a number Claim is denied, widen the IAM policy.
- Measure p95 turn time from the web chat's `agent_events` latency before
  switch-on. If it is near the bridge Lambda's 6 s backend timeout, add a
  voice-specific turn deadline or Lex progress updates first.
- The live test call must include a handoff to staff, to check that the
  transfer to the external number works.
- During the live test, check that the WAF rate limits (`terraform/waf.tf`)
  do not throttle the bridge Lambda's traffic (it reaches the app through
  CloudFront from a shared pool of Lambda addresses).
- The voice greeting is generic: `persona.greeting` is not used on voice
  yet.
- Restrict Amazon Connect's outbound destinations to the US and Canada
  (a Service Quotas / Support request sets the countries); the app also
  refuses any number outside them (`reservations/voice_transfers.py`).
- Set a maximum duration for a transferred call if Connect allows it for an
  external transfer; the provider's `aws_connect_contact_flow` has no such
  setting, so it would go in the flow content or the instance's settings
  in the console. If Connect has none, rely on the daily transfer cap and
  the budget, and note it here.
- Staff transfers are billed per minute: watch the voice budget, and add a
  CloudWatch alarm on Connect outbound minutes if transfers become common.
- Before turning `voice_provisioning_enabled` off, release the claimed
  numbers (see the README); with it off the app releases nothing.
- Expected costs: a Connect DID daily fee per number, plus per-minute
  inbound, Lex speech requests, Polly neural characters, and the $50 budget
  alarm. See AWS pricing for current rates.

### Call screening (step 7)

Schedule and Lambda notes are in the call screening section of
`terraform/README.md`.

- Verify `FTC_DNC_CSV_URL` against a live file. Our planning fetch got a
  404, likely bot filtering, so check from the ECS task or switch to the
  FTC API (decision 54).
- Set `ftc_ingest_enabled` and run the ingest once by hand. Expect an
  alert-free summary.
- Make a test call from a number on a block entry; it should hang up. Then
  call from an allowed number.
- Confirm the screening Lambda's cold start stays under 3 s. Calls still
  fail open if it does not (decision 47).
- A demo location can use `mock_external` (rules `provider`) to show the
  booking provider seam; keep real locations on `wizscheduler`.

### Analytics (step 10)

Setup steps are in `analytics/README.md` and the analytics lake section of
`terraform/README.md`.

- Set `analytics_enabled=true` and apply.
- `ANALYTICS_BUCKET` is set in the ECS task by `terraform/ecs.tf` when the
  flag is on; no manual setting is needed. Check the task definition shows
  it.
- Backfill from the first day reservations were enabled:
  `python -m backend.scripts.run_agent_analytics --backfill START END`, with
  the real dates (a failed day exits 1 and alerts; its lake is landed but
  its metrics are not written). Athena sees only days from 2026-11-01 on
  (the `dt` projection range, decision 75); change `projection.dt.range`
  first if you need earlier days.
- Run any backfill before changing `RESERVATIONS_USAGE_MARKUP`: the billing
  check recomputes billed amounts at the current markup, so a day from
  before a change fails `usage_billed` and its metrics are withheld. After a
  change, accept that alert for earlier days (`analytics/README.md`).
- Run `analytics/apply_views.sh` as the `wizscheduler-analytics-reader` role
  (assumed with MFA), then query each view once in the
  `wizscheduler-analytics` workgroup.
