# Reservations and an AI host agent (web + voice): design

Issues: #254 (reservation system), #259 (voice and web reservation agent).
Status: draft for owner approval, 2026-11-18.

## 1. Intent

Build a per-location table reservation system and an AI host that takes
bookings over web chat and the phone, inside WizScheduler, sold to any
company as its own subscription.

**Why.** There are no customers for this yet. The owner is building it to
expand a portfolio for data engineering and forward deployed engineer
(FDE) roles; the reference job is an FDE role at a company whose voice
agents follow each customer's scheduling rules to answer calls and book
visits. It must still be a real, sellable product: live in production,
opt-in per company, billed through its own subscription.

So the build optimizes for: one excellent end-to-end path, rules as
configuration, a clean integration seam, measurable agent quality, a real
analytics pipeline, and production-grade billing and abuse controls. It
does not optimize for breadth of integrations.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Managers define a floor plan per location (tables, seats, sections,
   combinable tables) and booking rules, and see today's floor and
   upcoming bookings.
2. Diners book, change or cancel through a public web chat or by calling
   the restaurant's agent phone number. The agent searches real
   availability, offers slots, gets a structured confirmation, and books;
   it hands off to staff when it should.
3. Booking rules are per-location configuration the agent follows without
   code changes.
4. The agent books through a provider interface. WizScheduler's own system
   is the first provider; a mock "external" provider proves the seam.
5. Any company can subscribe to the agent ($10/month, $5 usage included,
   metered overage at cost x 1.5). Subscribing provisions its phone
   number; cancelling releases it.
6. Incoming calls are screened before the agent answers, against the
   restaurant's own allow/block lists and a blocklist built from the FTC's
   Do Not Call reported-calls data.
7. Every conversation is recorded (transcript, tool calls, outcome,
   per-stage latency, cost) and replayable.
8. A scripted-conversation evaluation suite scores the agent, so every
   tuning change is a measured experiment.
9. Conversation events feed a batch analytics pipeline and a dashboard.
10. The voice channel can later be replaced by a hand-built streaming
    pipeline without touching the agent, rules, providers, evals, billing
    or analytics (section 6).

**Non-goals (this project)**

- Real third-party reservation systems (OpenTable, Resy, Tock, ...): their
  APIs need partner agreements. The mock provider stands in.
- Super-customer seating and dish holds (#256), caller recognition from
  order history (#258), customer-data encryption (#255).
- The $15 personal voice: AWS sells no self-serve custom voices. Personas
  are Polly preset voices.
- The "calendar booking service" that #259 says shares the $5 bucket does
  not exist; usage metering is keyed so a second product can share the
  bucket later (section 9), but nothing else is built for it.
- A diner-facing agent in languages other than English. Manager screens
  keep all 19 locales.
- Payments, deposits, no-show fees, waitlists, SMS.

**Anti-goals (failure even if it works)**

- An agent that books without an explicit, structured confirmation from
  the diner, or states availability that didn't come from code.
- A restaurant's phone number turning into an unbounded bill for it or
  for us (spam floods, toll fraud).
- Charging a restaurant for calls we blocked as spam.
- Any change that endangers the existing scheduling product or its
  billing: the new subscription is a separate money path, and the
  existing credit path is untouched.
- Voice-specific logic leaking into the agent core.

## 3. Constraints

- Lives in this repo and follows its conventions: FastAPI + SQLAlchemy 2.x
  `Mapped` models, 8-char string ids, `DateTime(timezone=True)`, Alembic,
  `company_id` tenancy with RLS, React frontend with i18n (19 locales for
  manager screens), Terraform for all AWS, CI/CD via `deploy.yml`.
- Times are the location's local time, as availability already is (#171);
  stored timestamps carry offsets derived from `location.timezone`.
  "Today" in code is UTC; the restaurant's local date is derived from the
  location zone where needed.
- **Billing convention change (deliberate, owner-approved).** CLAUDE.md
  says AI spend always debits purchased credits through
  `charge_saved_card`. The reservation agent is the exception: its model,
  speech and telephony costs are billed through its own Stripe
  subscription with a metered price, never through credits. CLAUDE.md and
  the architecture skill are updated in the same PR that adds the
  subscription. Scheduling AI keeps the credit path unchanged.
- Stripe money calls stay in `services/billing*.py` (the
  `stripe-outside-billing` ratchet): the agent subscription lives in a new
  `services/billing_reservations.py`.
- No new dependency without approval. This design asks for: `pyarrow`
  (Parquet, section 10). Connect, Lex, Polly, Lambda, Glue, Athena,
  Secrets Manager and Budgets are reached through Terraform and the
  existing `boto3`.
- Legal: diner contact data and call transcripts are a new category of
  personal data. Privacy policy and DPA wording is a separate draft PR
  held for legal review; the feature flag stays off in production until
  it is approved (same rollout as #218).
- Architecture skill: a new **Reservations** bounded context (section 5)
  with its own reference file; `thin-router`, `single-evaluator`,
  `aggregate-root` apply.

## 4. Approaches

**Voice channel** (owner chose A)

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | **Amazon Connect + Lex V2 + Polly**; a Lambda passes each turn to the backend agent | All in AWS/Terraform, no new vendor, what large contact centers run, many tuning knobs; turn-based, less low-level control of barge-in and latency |
| B | Twilio Media Streams + streaming STT + TTS, hand-built in a backend websocket | Deepest voice engineering; about three new vendors and the most work |
| C | Hosted voice-agent platform (Vapi, Retell) calling backend tools | Fastest; the interesting parts live in someone else's product |

A fits the AWS setup, adds no vendor, matches the AWS reference
architecture the owner pointed to, and leaves effort for rules,
integration, evaluation and data. B stays reachable through the channel
seam (section 6).

**Exposure** (owner chose: every company, separate subscription) over a
demo-company-only switch: it makes it a real product, at the cost of a
second billing path, per-company numbers, spam controls and a legal
update, all designed below.

**Model provider:** the Anthropic SDK already used by the scheduler (my
call), not Bedrock.

## 5. Domain: the Reservations context

`backend/reservations/` (models, services, provider interface, agent),
routers `reservations` (manager), `public_reservations` (diner web chat)
and `voice_bridge` (Lambda-only), reference file
`.claude/skills/architecture/references/reservations.md`.

**Data model** (all `company_id`-scoped, RLS like other tenant tables)

- `floor_sections`: location, name, sort order.
- `dining_tables`: location, section, label, min/max seats, combinable,
  editor geometry (x, y, w, h, rotation), `archived_at` (archive, never
  delete, as #126).
- `table_combinations`: a named set of combinable tables and its seat
  range.
- `reservation_rules` (one per location, JSONB validated by a Pydantic
  schema, versioned): service periods per weekday, slot interval, dining
  duration by party size, max party size for self-booking, party size
  needing staff approval, booking horizon and cut-off, churn (table
  reset) time per party-size band, restaurant capacity (max diners seated
  at once) and pacing (max covers starting per slot), closures and
  date-specific hour overrides, per-section limits, hold timeout (default 5 min),
  pending-approval hold (default 24 h), agent persona (Polly voice,
  greeting, restaurant facts the agent may state), staff transfer number.
- `reservations`: location, party size, start/end (tz-aware), table or
  combination, status (`held`, `pending_approval`, `booked`, `seated`,
  `completed`, `no_show`, `cancelled`), diner name, phone, email, notes,
  source (`web_agent`, `voice_agent`, `staff`), **confirmation code**
  (6 characters, unique per location), provider + external id,
  conversation id.
- No `diners` table yet: contact lives on the reservation; #256 owns
  customer identity later.

**Single evaluator:** `reservations.availability.find_slots(location,
party_size, window) -> list[Slot]` is the only code that decides what is
bookable (rules, periods and date overrides, durations, churn, restaurant
capacity and pacing, closures, existing
reservations, holds, table fit and combinations). Agent, web UI, manager
views and the WizScheduler provider all call it. Booking re-checks it
under a row lock on the location's reservation day, and a Postgres
exclusion constraint on (table, time range) for `held`,
`pending_approval` and `booked` rows backs it up.

**Holds:** offering a slot creates a `held` row for the hold timeout. A
`pending_approval` request (large party, or no acceptable offer) also
holds its table for up to 24 hours or until staff approve or decline.
Expired holds are ignored by `find_slots` and swept.

**Manager and host UI:** floor plan editor (SVG, no new library), rules
editor with a slot preview, host view (today's floor, timeline, seat /
complete / no-show / cancel, approval queue), conversations view
(section 8), number lists (section 7), subscription page (section 9).

## 6. The agent and its channels

```
           web chat (React widget)           phone (Amazon Connect)
                     |                          |  1. screening Lambda (section 7)
                     |                          |  2. Lex V2 (speech <-> text)
                     |                          |  3. turn-bridge Lambda
                     v                          v
         public turn endpoint              voice bridge endpoint (secret)
                     \                          /
                AgentCore.handle_turn(conversation, input, channel)
                                  |
              Claude (tool use)  -+-  tools: find_slots, hold_slot, book,
                                  |   modify, cancel, lookup_booking,
                                  |   handoff_to_staff, restaurant_info
                                  v
                   BookingProvider (wizscheduler | mock_external)
```

**AgentCore** (`backend/reservations/agent/`) is channel-agnostic: a turn
in, a reply plus structured events out. It loads the location's rules and
persona, keeps conversation state in Postgres (not in the Lambda or Lex
session), runs the Claude tool loop, enforces the rules of conduct, and
records every step. It knows nothing about Lex, Connect, SSML or
websockets.

**Turn contract.** Input: `{text | structured_action, channel, caller_id?}`.
`structured_action` carries a structured confirmation (`confirm_hold`,
`decline_hold`). Output: `{reply_text, expect: ChannelHints, events}`.
`ChannelHints` says what input is expected next (free text, a yes/no
confirmation of a named hold, a party size, a phone number, a
confirmation code), so a channel can render a button, run a Lex
confirmation, bias recognition, or offer the keypad.

**Rules of conduct, enforced in code**

- Availability is only ever stated from a `find_slots` result in the same
  conversation.
- `book` is accepted only for a hold that the diner confirmed with a
  structured `confirm_hold` for that hold id: a Confirm button on web; on
  voice, a Lex confirmation prompt ("Friday the 21st at 7 pm for 4, shall
  I book it?") whose yes/no Lex resolves and the bridge sends as the
  structured action. The model cannot produce a confirmation itself.
- `lookup_booking`, `modify` and `cancel` require the confirmation code
  plus a matching phone number. On voice, caller ID supplies the phone
  number when it matches; otherwise the diner states it.
- Requests over the self-booking party limit, or two declined offers, go
  to `handoff_to_staff`: voice transfers to the staff number; web creates
  a `pending_approval` request.
- Voice replies are capped short (max tokens and style rule); Haiku is the
  default voice model.

**Channel adapters**

- *Web:* public chat widget per location, embeddable; Confirm/Change
  buttons rendered from `ChannelHints`; replies streamed.
- *Voice (A):* Connect flow -> screening Lambda -> Lex V2 bot (one
  catch-all intent plus a confirmation step) -> bridge Lambda -> backend
  `voice_bridge` endpoint, authenticated with a secret from Secrets
  Manager. The bridge renders SSML, applies `ChannelHints` as Lex runtime
  hints or DTMF, and reports per-stage timings. Connect handles retries,
  after-hours, and transfer.

**Swap seam.** The turn contract is the only interface between a channel
and the rest of the system. A future streaming channel (Twilio Media
Streams + streaming STT/TTS) is a new adapter calling the same endpoint;
AgentCore, rules, providers, billing, evals and analytics are unchanged.
Latency is recorded per stage (`screen`, `asr`, `llm`, `tools`, `tts`,
`transport`) so channels compare directly.

**Providers.** `BookingProvider` protocol: `find_slots`, `hold`, `book`,
`modify`, `cancel`, `lookup`. `WizSchedulerProvider` calls the
Reservations services. `MockExternalProvider` owns its own inventory in
separate `mock_external_*` tables with its own ids, injected latency,
configurable failure rate and idempotency keys, behaving like a
third-party API; its bookings appear in the host view through a
read-through listing, marked as external. Location config selects the
provider.

**Tuning surface:** Lex custom vocabulary, slot synonyms, runtime hints,
confidence threshold, silence timeouts, max utterance length, barge-in,
DTMF; Polly voice and SSML; Claude model per turn type, prompt caching,
reply length, prompt and tool wording, handoff thresholds; Connect
retries and transfer; Contact Lens (with PII redaction) on.

**Latency targets (voice, per turn):** p50 under 1.5 s, p95 under 3 s,
measured by the eval suite. A filler prompt ("one moment, checking
Friday") plays when a tool call is expected to exceed 1 s.

## 7. Call screening

Runs in Connect before Lex, Polly or Claude engage; blocked calls are not
usage.

**Order of checks** (first match wins):

1. The restaurant's **allow** entries -> answer.
2. The restaurant's **block** entries -> hang up.
3. The **FTC blocklist** -> hang up.
4. Otherwise answer.

**Restaurant number lists** (`reservation_number_rules`): per location
(with an option to apply to all of the company's locations), phone number
(E.164), action `block` | `allow`, `expires_at` (null = permanent), note,
created by, created at. Expired entries stop applying but stay in the
list history. Marking a conversation as spam in the conversations view
creates a permanent `block` entry, which the manager can shorten or
remove.

**FTC blocklist** (`spam_numbers`, global, not tenant-scoped):

- Source: the FTC Do Not Call **Reported Calls Data** daily CSV files
  (published each weekday). CSV downloads, not the API, so no API key.
- A scheduled ECS task runs **weekly** to start and downloads every
  weekday file since its last successful run, so a weekly cadence loses
  nothing. Cadence is one Terraform variable; switch to daily once there
  are more than two subscribers.
- A number is blocked when reported at least **3 times in the last 90
  days** (configurable); entries not reported for 90 days expire. Reports
  are unverified and numbers are often spoofed, so the threshold and the
  manager allow list limit false blocks.
- Ingest is idempotent per source file (file date recorded), and a run
  that downloads nothing or parses zero rows alerts the operator.

**Lookup path:** the screening Lambda calls a `voice_bridge` screening
endpoint with the dialed and calling numbers; the backend answers from
Postgres (indexed). If the backend doesn't answer within 1 s the call is
answered (fail open: a missed spam call costs cents, a dropped diner
costs a booking). Calls with a withheld caller ID are answered.

Web chat has no caller number: it is protected by per-IP and
per-conversation rate limits, a turn cap per conversation, and the
per-company spend cap (section 9).

## 8. Observability and evaluation

**Recording.** `agent_conversations` (location, channel, caller number,
started/ended, outcome `booked` / `modified` / `cancelled` / `handed_off`
/ `abandoned` / `failed` / `blocked`, reservation id, cost breakdown,
billed amount) and `agent_events` (conversation, seq, type `user_turn`,
`assistant_turn`, `tool_call`, `tool_result`, `error`, `handoff`; payload
JSONB; per-stage latency; tokens; cost). Blocked calls get a
conversation row with outcome `blocked` and no billed amount.

**Conversations view.** Transcripts with tool calls, outcome, latency and
cost; filter to failures; "mark as spam" action.

**Evaluation suite** (`backend/reservations/evals/`):

- Scenarios as YAML: diner persona and goal (book 4 Friday at 7, change a
  booking with its code, party of 14, ambiguous date, mid-call change of
  mind, outside service hours, fully booked night, wrong confirmation
  code), seeded floor and rules, expected end state.
- A simulated diner (cheap Claude model) talks to AgentCore through the
  turn contract; a deterministic checker scores the database end state
  (right booking or right handoff, no double booking, no unconfirmed
  booking, no lookup without the code), turns taken and cost.
- Voice runs: Polly-synthesized diner lines through the real Lex bot to
  measure recognition and per-stage latency.
- Runs are stored (`eval_runs`, `eval_results`) with their config (model,
  prompt version, Lex settings) for comparison; a report shows pass rate,
  first-try understanding, p50/p95 turn latency, cost per booking.
- **CI** runs the deterministic checks against recorded transcripts and a
  stubbed model (no API key, no cost). Live runs against Claude and Lex
  are run by hand from the CLI; their results are stored and reported.

## 9. Billing: the agent subscription

`services/billing_reservations.py` (inside Billing & Plans).

- **Product:** a Stripe Product "AI host agent" with two prices on one
  subscription, separate from the scheduling subscription: a $10/month
  licensed price, and a metered price billed per cent of overage.
  Config: `RESERVATIONS_PRICE_ID`, `RESERVATIONS_METERED_PRICE_ID`,
  `RESERVATIONS_INCLUDED_USAGE_USD` (5.00), `RESERVATIONS_USAGE_MARKUP`
  (1.5).
- **Sign-up:** an admin subscribes from the billing page via Stripe
  Checkout (`mode=subscription`) on the ownership group's existing Stripe
  customer. Any plan may subscribe, free scheduling plans included. The
  subscription id is stored on a new `reservation_subscriptions` row tied
  to the ownership group (status, period, phone number id).
- **Provisioning:** on the subscription becoming active (webhook), a
  provisioning job claims a Connect phone number, associates it with the
  agent flow, stores it, and shows it to the manager. On cancellation or
  final payment failure the agent stops answering, and the number is
  released after a grace period (7 days) so a quick resubscribe keeps it.
- **Usage:** every conversation that reaches the agent (outcome not
  `blocked`) is usage, whether or not it books. Cost = Claude tokens at
  list price + Lex + Polly + Connect minutes, from recorded counts and
  configured unit prices. Billed amount = round(cost x 1.5, to the cent)
  per conversation. The first $5.00 of billed amount in each billing
  period is included; beyond it, usage is reported to Stripe as metered
  quantity (cents) with an idempotency key per conversation, from a
  periodic job (not inline in the call), so a Stripe outage never affects
  a live call.
- **Shared bucket hook:** usage rows carry a `product` key
  (`reservation_agent`), and the included-usage calculation sums by
  ownership group across products, so a future product can share the $5.
- **Safety caps:** a per-location daily spend cap (default $20 of cost,
  configurable by the admin) after which the agent stops answering new
  conversations (voice: a message and transfer to the staff number; web:
  a message with the restaurant's phone number). An operator alert fires
  on any group's unusual daily usage, and an AWS Budgets alarm covers
  Connect overall.
- **Invariants:** blocked calls are never billed; a conversation is
  reported to Stripe at most once; reporting never raises into the call
  path; the scheduling subscription and credit path are not touched.

## 10. Analytics pipeline (data engineering)

- **Extract:** a nightly ECS scheduled task (same pattern as the retention
  and Stripe reconcile jobs) reads the previous UTC day's conversations,
  events, reservations, usage and eval results.
- **Land:** partitioned Parquet (`dt=YYYY-MM-DD/`) in a new private S3
  bucket (`pyarrow`, needs approval), idempotent per partition. The lake
  holds no diner names, phone numbers or emails: conversation and
  reservation ids only, transcripts redacted before landing.
- **Model:** Glue Data Catalog tables, queryable in Athena; SQL models for
  daily resolution rate, booking conversion, handoff reasons, latency
  percentiles, cost and billed amount per booking, spam-blocked share,
  covers by service period.
- **Serve:** the same job writes daily rollups to a Postgres
  `agent_daily_metrics` table behind an in-app dashboard; the app never
  queries Athena at request time.
- Backfill command for a date range; data-quality checks (row counts match
  source, latencies non-negative, every `booked` conversation has a
  reservation, billed amounts reconcile with Stripe usage records) fail
  the job loudly.

## 11. Security and privacy

- Public chat endpoints are keyed by company slug + location id,
  rate-limited per IP and per conversation, turn-capped, and act through
  a session scoped to that one company (never unscoped).
- The voice bridge and screening endpoints are reachable only with the
  Lambda secret; the Lambdas have no database access.
- Diner data and transcripts: included in the company's GDPR export;
  erasable per reservation (by confirmation code or phone number) from
  the manager UI; retained 1 year by the retention job. Contact Lens and
  Lex logs use PII redaction, and their retention is set to match.
- Legal: privacy policy and DPA updates as a separate draft PR held for
  legal review; `RESERVATIONS_ENABLED` stays off in production until it
  is approved.

## 12. Delivery order

Each step is a separate PR behind `RESERVATIONS_ENABLED` (off in
production until legal approval) and works on its own:

1. Reservations core: models, migration, `find_slots`, holds, booking with
   lock and exclusion constraint, rules schema, manager API, tests.
2. Manager UI: floor plan editor, rules editor, host view (19 locales).
3. AgentCore + web channel + structured confirmation + recording +
   conversations view.
4. Evaluation suite (text scenarios, CI on stubs) and a first measured
   baseline.
5. Billing: agent subscription, Checkout, webhooks, usage metering and
   reporting, caps, CLAUDE.md and architecture-skill update.
6. Voice: Terraform for Connect, Lex, Polly, Lambdas, secret, budget;
   number provisioning and release; bridge; SSML; transfer.
7. Call screening: number lists UI, FTC ingest job, screening Lambda.
8. Voice evals (synthesized audio) and a tuning round with recorded
   results.
9. Mock external provider and provider selection.
10. Analytics pipeline, Glue/Athena, dashboard.
11. Legal draft PR (held), then the production switch-on, then a public
    demo location seeded with a realistic restaurant.

## 13. Decided by the owner

- Build #254 and #259 together; first target is WizScheduler's own
  reservations.
- Purpose: portfolio for data engineering and FDE roles, and a real
  product.
- Web and voice ship together.
- Voice approach A (Amazon Connect + Lex + Polly).
- The voice channel must be swappable later for a more hands-on pipeline.
- Open to every company as a separate subscription ($10/month, $5
  included, cost x 1.5 overage), a deliberate change to the
  credits-only rule.
- Companies sign up for it; subscribing provisions their number.
- Every call that reaches the agent counts as usage.
- Calls are screened before answering against the restaurant's lists and
  the FTC reported-calls list; the FTC ingest need not be daily until
  there are more than two customers.
- Managers can block or allow numbers permanently or until a date, with an
  optional note.
- Availability checks restaurant capacity as well as table fit; seating is
  configurable (table combinations seat large parties); hours include
  date-specific overrides; churn time between seatings is configurable.
- Any company may subscribe, free scheduling plans included, since it is a
  separate charge.

## 14. My calls (owner to check)

- Anthropic SDK for the agent, not Bedrock; Haiku default on voice.
- Personas are Polly preset voices; the $15 personal voice is dropped.
- Rounding to the cent per conversation; blocked calls never billed.
- Structured confirmation (button / Lex confirmation) is required to book.
- Lookup, change and cancel need the confirmation code plus phone number.
- English-only diner agent.
- Per-location daily spend cap, default $20 of cost.
- FTC threshold: 3 reports in 90 days; 90-day expiry; fail open at 1 s.
- Released numbers held 7 days after cancellation.
- Mock external provider keeps its own inventory.
- `pending_approval` holds its table up to 24 hours.
- CI evals on stubs; live evals by hand.
- Lake holds no direct diner identifiers.
- New dependency requested: `pyarrow`.

## 15. Left to the builder

Exact column types and indexes, the SVG editor's interaction details,
prompt wording, the Lex vocabulary and hints, scenarios beyond those
named, Athena model names, dashboard layout, unit-price table values.
