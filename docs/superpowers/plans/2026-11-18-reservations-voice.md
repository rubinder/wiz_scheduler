# AI Host Voice Channel (Amazon Connect) Implementation Plan (step 6 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diners can phone a restaurant's AI host: Amazon Connect answers,
Lex V2 turns speech into text and back, a small Lambda bridges each turn to
the backend's `AgentCore`, and numbers are provisioned per location when a
company subscribes. All infrastructure ships switched off.

**Architecture:** The voice channel is an adapter around the existing turn
contract (spec section 6). Backend: a Lambda-only `voice` router (shared
secret, constant-time compare) maps the dialed number to a location, opens
or continues the conversation for the Connect contact id (channel `voice`),
claims the turn like the web route, runs `AgentCore.handle_turn`, and
returns text plus hints. The Lambda (Python stdlib only) converts Lex V2
code-hook events to that call and renders the reply as SSML with the right
Lex dialog action (confirm, elicit, close, transfer). Terraform defines
Connect, Lex, the Lambda, IAM, the secret and a budget alarm, every resource
behind `var.voice_enabled` (default `false`).

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, boto3 (installed) for Connect
number provisioning, Python 3.11 stdlib for the Lambda, Terraform aws
provider ~> 5 (no new providers: the deploy workflow zips the Lambda with
the `zip` CLI and passes the path).

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
sections 6, 7 (screening is step 7), 9 (provisioning), 12 step 6.

## Global Constraints

- No new Python dependency and no new Terraform provider.
- Every new Terraform resource has `count = var.voice_enabled ? 1 : 0`;
  `voice_enabled` defaults to `false`; `terraform plan` on main must show
  no voice resources. Nothing billable is created by merging.
- Number provisioning (Connect ClaimPhoneNumber / ReleasePhoneNumber) runs
  only when `settings.VOICE_PROVISIONING_ENABLED` is true (default false).
- The bridge endpoint is never public: requests without the exact
  `X-Voice-Bridge-Secret` (settings.VOICE_BRIDGE_SECRET, non-empty) get 404;
  compare with `hmac.compare_digest`.
- The Lambda has no database access; it only calls the backend.
- Voice reuses every web guarantee: subscription gate, spend cap, in-flight
  cap, atomic turn claim, structured confirmation (Lex confirmation maps to
  the `confirm_*` actions), handoff → transfer to the location's staff
  number.
- Voice latency stages recorded on events: `asr`, `tts` (from the Lambda's
  timing fields), `llm`, `tools`, `transport`.
- English only (`en_US` Lex locale).

## Review Focus

1. **A spoken "yes" must never book unless Lex's confirmation step
   resolved it** (Lex `confirmationState == Confirmed` → `confirm_hold`
   action; free text "yes" is still text). Test in Tasks 2–3.
2. **Unknown dialed number / unsubscribed company**: the bridge answers a
   polite "This line isn't taking bookings right now" and ends the call,
   no model call. Test in Task 2.
3. **Repeated Lex retries of the same turn** (Lex retries a code hook on
   timeout): the backend dedupes by `(contact_id, turn sequence)` so a
   retry returns the cached reply instead of running the model twice.
   Test in Task 2.
4. **Phone numbers held after cancel**: released 7 days after the
   subscription ends, never while active. Test in Task 1.
5. **Terraform with voice off creates nothing.** Checked in Task 4 by
   `terraform plan` output in CI (no `aws_connect`/`aws_lexv2models`
   resources) — plus a `terraform validate` with voice on.

---

### Task 1: Voice numbers and provisioning service

**Files:** `backend/reservations/voice_numbers.py` (model + service), migration `0060_voice_numbers.py`, `backend/services/connect_numbers.py` (boto3 Connect calls, behind the flag), hooks in `services/billing_reservations.py` (on agent subscription active/canceled), manager route `GET /api/v1/reservations/locations/{id}/voice` (number, status), tests.

**Interfaces:**
- `VoiceNumber(id, company_id, ownership_group_id, location_id unique, e164 unique, connect_phone_number_id, status "requested"|"active"|"releasing"|"released", release_after nullable, created_at, updated_at)` + RLS (company-keyed).
- `connect_numbers.claim(country="US", prefix=None) -> (e164, phone_number_id)`; `associate(phone_number_id, contact_flow_id)`; `release(phone_number_id)` — boto3 `connect` client; settings `CONNECT_INSTANCE_ARN`, `CONNECT_CONTACT_FLOW_ID`; never called when `VOICE_PROVISIONING_ENABLED` is false (returns "disabled").
- `voice_numbers.ensure_for_group(db, og_id)`: on subscription answering → claim one number per active location without one; on terminal status → mark `releasing` with `release_after = now + 7 days`; a daily step (added to `run_agent_overage_reconcile.py`) releases due ones; resubscribe within 7 days reactivates without a new claim.
- `voice_numbers.location_for_number(db, e164) -> (company_id, location_id) | None` — cross-tenant lookup via `cross_tenant_session` (read-only, exact match).

- [ ] Tests (boto3 client faked): claim on subscribe, no claim when flag off, 7-day hold and release, reactivate within hold, archived location excluded, lookup.
- [ ] Commit `feat(voice): numbers per location and Connect provisioning (#259)`

### Task 2: Voice bridge API

**Files:** `backend/routers/voice_bridge.py`, `backend/schemas/voice.py`, `backend/reservations/agent/recording.py` (voice conversation per contact id; dedupe cache), tests.

**Interfaces:**
- `POST /api/v1/voice/turn` (404 unless secret matches and RESERVATIONS_ENABLED): body `{contact_id: str (1..128), dialed: E164, caller: E164|null, seq: int ≥ 0, text: str|null, action: confirm_hold|decline_hold|confirm_cancel|confirm_modify|null, timings: {asr_ms?: int, tts_ms?: int}}` → `{text, expect, hints: {elicit: "party_size"|"phone_number"|"confirmation_code"|"date"|"time"|null, dtmf: bool}, confirm_prompt: str|null, end_call: bool, transfer_to: E164|null, reason: str|null}`.
- Number → location via `location_for_number`; then the same gate as web (subscription 404-equivalent → polite close; cap → busy close; in-flight → "one moment, please say that again").
- Conversation: first turn for a contact id creates the conversation (`channel="voice"`, `caller_number=caller`, `source_key` = HMAC of the caller number or "anonymous"); later turns load it by `(location, contact_id)` — store contact id on the conversation (new nullable unique column `external_ref` in 0060).
- Dedupe: `(conversation, seq)` — if the last recorded turn has the same seq, return the stored reply without running the core (store last reply JSON + seq on the conversation).
- `confirm_prompt`: when `expect` is `confirm_hold`/`confirm_modify`/`confirm_cancel`, a short spoken question built from structured data (hold/proposal), e.g. "Shall I book a table for 4 on Friday the 21st at 7 pm?".
- Handoff on voice: `transfer_to` = location staff transfer number (rules), `end_call` false; if none, a closing line and `end_call` true.
- Timings recorded as latency stages on the turn's events.

- [ ] Tests: secret missing/wrong → 404 (constant-time path), unknown number → close without model call, unsubscribed → close, a booking via text turns + `confirm_hold` action, free "yes" text doesn't book, duplicate seq returns cached reply with one model call total, handoff → transfer number, timings stored.
- [ ] Commit `feat(voice): Lambda bridge endpoint for Connect turns (#259)`

### Task 3: Lex code-hook Lambda

**Files:** `infra/voice/bridge/handler.py`, `infra/voice/bridge/ssml.py`, `tests/infra/test_voice_bridge.py` (pure, no AWS, no network — inject the HTTP call).

**Interfaces:**
- `handler(event, context)`: Lex V2 code-hook event → (session attributes keep `seq`, `contact_id` from `sessionState.sessionAttributes` set by the contact flow, `dialed`, `caller`) → POST to `BACKEND_URL/api/v1/voice/turn` (urllib, 4 s timeout, secret from env populated from Secrets Manager at cold start via boto3 in the Lambda runtime — boto3 is provided by the Lambda runtime, not a repo dependency) → Lex response:
  - `confirm_prompt` → `dialogAction.type = "ConfirmIntent"` with SSML message; next invocation's `confirmationState` Confirmed/Denied → action `confirm_*` / `decline_hold`;
  - `hints.elicit` → `ElicitSlot` on the catch-all intent's matching slot with runtime hints; `dtmf` → session attributes enabling DTMF for that slot;
  - `end_call` → `Close`; `transfer_to` → `Close` with session attribute `transfer_to` (the contact flow reads it and transfers);
  - else → `ElicitIntent` with the reply.
  - Backend timeout or 5xx → "Sorry, I'm having trouble — let me transfer you" + transfer if a number is known, else close.
- `ssml.render(text) -> str`: escape XML; speak times ("7:00 PM" → "7 pm"), dates ("2026-11-21" → "Friday, November 21st"), E.164 phone numbers as digits groups, confirmation codes letter by letter (`<say-as interpret-as="characters">`); wrap in `<speak>`.
- [ ] Tests: each dialog action mapping; confirmation flow; timeout fallback; SSML rendering cases; XML escaping of model text (no injection of SSML tags from model output).
- [ ] Commit `feat(voice): Lex V2 code-hook Lambda and SSML rendering (#259)`

### Task 4: Terraform and deploy packaging

**Files:** `terraform/voice.tf`, `terraform/variables.tf` (`voice_enabled` false, `voice_budget_usd` 50), `terraform/voice/contact_flow.json.tftpl`, `terraform/ecs.tf` (inject `VOICE_BRIDGE_SECRET` secret and `CONNECT_*` env only when enabled — use conditional lists), `.github/workflows/deploy.yml` (zip `infra/voice/bridge` into `build/voice_bridge.zip` before terraform plan/apply; pass `-var voice_bridge_zip=...`), `terraform/README.md` (voice section).

**Resources (all count-gated):** `aws_connect_instance` (inbound calls on, outbound off), `aws_lexv2models_bot` + `_bot_locale` (en_US, Polly voice from a variable, default "Joanna") + `_intent` (one catch-all `HostTurn` intent with a free-text slot and fallback routing to the code hook) + `_bot_version`, Lex bot alias with the Lambda code hook (via `aws_lexv2models_bot_alias` if available in the pinned provider, else document a one-time CLI step in the README), `aws_lambda_function` (python3.11, 256 MB, 10 s timeout, env BACKEND_URL, SECRET_ARN), IAM role + policy (logs, read the one secret), `aws_lambda_permission` for Lex, `aws_connect_bot_association`, `aws_connect_contact_flow` from the template (set session attributes contact_id/dialed/caller → Lex → on `transfer_to` attribute transfer to number → disconnect), `aws_secretsmanager_secret` for the bridge secret (value set by the owner), `aws_budgets_budget` with an email alert at 80% of `voice_budget_usd`.

- [ ] `terraform fmt -check`, `terraform validate` with voice off (CI's plan must show no voice resources) and with `-var voice_enabled=true` locally (validate only, no apply).
- [ ] Commit `feat(voice): Terraform for Connect, Lex and the bridge Lambda, off by default (#259)`

### Task 5: Docs and checklist

- Decisions log: 40 (voice off by default; numbers claimed per location on subscribe, released 7 days after cancel), 41 (Lambda zipped by the workflow, no new provider).
- Owner switch-on checklist: confirm Connect + Lex availability in the region; set the bridge secret value; set `voice_enabled = true` and apply; publish the Lex bot version/alias if the provider can't; set `CONNECT_INSTANCE_ARN`/`CONNECT_CONTACT_FLOW_ID`; set `VOICE_PROVISIONING_ENABLED=true`; one live test call; costs (Connect number daily fee + per-minute, Lex, Polly).
- Architecture skill: voice bridge entry points.
- [ ] Commit `docs(voice): switch-on checklist and decisions (#259)`
