# AI Host Agent + Web Chat Implementation Plan (step 3 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A channel-agnostic AI host (`AgentCore`) that books, changes and
cancels reservations by talking to diners, a public web chat that uses it,
full conversation recording, and a manager conversations view.

**Architecture:** `backend/reservations/agent/` holds the core: a manual
Claude tool-use loop (Anthropic Python SDK 2.x, `AsyncAnthropic`) whose
tools call the Reservations services. Conversation state lives in Postgres
(`agent_conversations`, `agent_events`). Booking is never a model tool: a
hold is confirmed only by a structured `confirm_hold` action from the
channel (a button on web). Public endpoints bind the request to one
company (tenant context + RLS) after resolving the company slug. The model
client is injected so tests run against a scripted fake.

**Tech Stack:** FastAPI, SQLAlchemy 2.x, Alembic, `anthropic` 2.10 (already
installed), React 18, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
(sections 6, 8, 11; section 12 step 3). Depends on step 1
(`backend/reservations/`) and uses step 2's `api/reservations.ts` types if
merged (otherwise defines its own).

## Global Constraints

- Model: `settings.RESERVATION_AGENT_MODEL = "claude-haiku-5-5"`; thinking
  left at its default (adaptive) with `output_config={"effort": "low"}`;
  `tool_choice={"type": "auto"}` (forced tool choice is not used); every
  tool `strict: True` with `additionalProperties: False`; tool inputs parsed
  as JSON objects from the SDK, never string-matched.
- Prompt caching: top-level `cache_control={"type": "ephemeral"}`; the
  system prompt is byte-stable per location rules version (no timestamps
  in it). "Today/now" goes in a per-turn user-side context line, not the
  system prompt.
- `stop_reason == "refusal"` or `"max_tokens"` ends the turn with a
  handoff message; never raise out of `handle_turn`.
- Cost per model call = `input_tokens × RESERVATION_AGENT_INPUT_USD_PER_MTOK
  / 1e6 + output_tokens × RESERVATION_AGENT_OUTPUT_USD_PER_MTOK / 1e6`
  (+ cache read/write tokens at their configured rates), with config
  defaults 0.10 / 0.50 / cache-read 0.01 / cache-write 0.125 USD per MTok.
  Recorded on events; billing is step 5.
- Availability is only ever stated from a `find_slots` result in the same
  conversation (`single-evaluator`).
- Every query filters by `company_id`; public routes resolve the company by
  slug, then call `set_tenant_context([company_id])` and set
  `db.info[RLS_COMPANY_IDS] = [company_id]` before touching tenant tables.
- Diner-facing text is English only. Manager UI strings in 19 locales.
- "now" = `datetime.now(timezone.utc)`; no new dependencies.
- `RESERVATIONS_ENABLED` false → every new route 404s.

## Review Focus

1. **A diner says "yes" in free text without the button.** The agent must
   not book; it re-presents the hold with the Confirm action. Test in Task 3.
2. **The model invents a time or a table** not in a `find_slots` result:
   `hold_slot` for a start not offered in this conversation is rejected
   with a tool error the model sees. Test in Task 2.
3. **Hold expires before the diner confirms**: `confirm_hold` returns a
   friendly "that hold expired" reply and re-offers. Test in Task 3.
4. **Someone else's booking**: lookup/modify/cancel with a right code but a
   wrong phone number reveals nothing. Test in Task 2.
5. **Abuse of the public endpoint**: turn cap (30 per conversation), per-IP
   limit (60 turns/hour), message length cap (1000 chars) all answer 429/422
   without calling the model. Test in Task 4.

---

## File Structure

```
backend/reservations/agent/__init__.py
backend/reservations/agent/models.py        # AgentConversation, AgentEvent
backend/reservations/agent/recording.py     # start_conversation, record_event, finish
backend/reservations/agent/tools.py         # tool schemas + execute_tool
backend/reservations/agent/prompt.py        # build_system_prompt(rules, location)
backend/reservations/agent/core.py          # AgentCore.handle_turn
backend/reservations/agent/llm.py           # LlmClient protocol + AnthropicLlm
backend/routers/public_reservations.py      # diner web endpoints
backend/routers/reservations.py             # + GET conversations, GET conversation
backend/schemas/reservation_agent.py
backend/alembic/versions/0058_agent_conversations.py
backend/config.py                           # agent settings
backend/services/data_retention.py          # + reservations/conversations purge
backend/routers/gdpr.py                     # + diner data in company export
tests/reservations/agent/...                # per task
frontend/src/api/publicReservations.ts
frontend/src/pages/ReserveChat.tsx          # public route /r/:slug/:locationId
frontend/src/pages/manager/Conversations.tsx
frontend/src/utils/chat.ts (+ .test.ts)
```

---

### Task 1: Conversation models and recording

**Files:** Create `agent/models.py`, `agent/recording.py`, migration `0058_agent_conversations.py`, `tests/reservations/agent/test_recording.py`; modify `backend/models/__init__.py`, `backend/config.py`.

**Interfaces:**
- `AgentConversation(id, company_id, location_id, channel: "web"|"voice", public_token: str (unique, 43 chars, `secrets.token_urlsafe(32)`), caller_number: str|None, status: "open"|"closed", outcome: str|None ("booked","modified","cancelled","handed_off","abandoned","failed","blocked"), reservation_id: str|None, held_reservation_id: str|None, turn_count: int, verified_reservation_id: str|None, offered_starts: list[str] (JSONB, ISO instants offered this conversation), cost_usd: Numeric(12,6), started_at, ended_at|None, created_at, updated_at)`.
- `AgentEvent(id, company_id, conversation_id FK cascade, seq: int (unique per conversation), type: "user_turn"|"assistant_turn"|"tool_call"|"tool_result"|"error"|"handoff"|"action", payload: JSONB, latency_ms: dict JSONB (keys from `screen`,`asr`,`llm`,`tools`,`tts`,`transport`), input_tokens, output_tokens, cache_read_tokens, cache_write_tokens: int default 0, cost_usd: Numeric(12,6) default 0, created_at)`.
- Settings: `RESERVATION_AGENT_MODEL`, the four price settings above, `RESERVATION_AGENT_MAX_TURNS = 30`, `RESERVATION_AGENT_MAX_TOOL_ROUNDS = 6`, `RESERVATION_AGENT_MAX_MESSAGE_CHARS = 1000`, `RESERVATION_AGENT_IP_TURNS_PER_HOUR = 60`, `RESERVATION_AGENT_MAX_TOKENS = 1024`.
- `recording.start_conversation(db, company_id, location_id, channel, caller_number=None) -> AgentConversation`; `recording.record_event(db, conv, type, payload, *, latency_ms=None, usage=None) -> AgentEvent` (assigns next `seq` under `SELECT ... FOR UPDATE` on the conversation row, adds cost to `conv.cost_usd`); `recording.finish(db, conv, outcome)`; `recording.cost_of(usage) -> Decimal` using the settings.
- Migration with the 0053 RLS block for both tables.

- [ ] **Step 1: Failing tests**: seq increments 1,2,3; cost accumulates exactly (`Decimal`) for given token counts; public_token unique and 43 chars; finish sets outcome/status/ended_at.
- [ ] **Step 2: Run** `backend/.venv/bin/python -m pytest tests/reservations/agent/test_recording.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it plus `tests/test_migrations.py` — PASS.
- [ ] **Step 5: Commit** `feat(agent): conversation models and recording (#259)`

---

### Task 2: Agent tools

**Files:** Create `agent/tools.py`, `tests/reservations/agent/test_tools.py`.

**Interfaces:**
- `TOOLS: list[dict]` — Anthropic tool definitions, each `strict: True`, schemas with `additionalProperties: False` and `required`:
  - `find_slots {party_size: int, date: str "YYYY-MM-DD", earliest: str|null "HH:MM", latest: str|null "HH:MM"}` — local date/time window → `find_slots(apply_cutoff=True)`; returns up to 6 options `[{start_iso, local_time, local_date}]`, appends their `start_iso` to `conv.offered_starts`.
  - `hold_slot {start_iso: str, party_size: int, name: str, phone: str}` — rejects (tool error `not_offered`) unless `start_iso` is in `conv.offered_starts`; validates E.164 phone; calls `booking.hold(source=web_agent|voice_agent)`, stores name/phone on the held reservation (diner fields) and `conv.held_reservation_id`; returns `{hold_id, local_time, local_date, party_size, status: "held"|"pending_approval", expires_in_minutes}` and signals `expect=confirm_hold`.
  - `lookup_booking {confirmation_code: str, phone: str}` → `booking.lookup`; on success sets `conv.verified_reservation_id`; returns booking summary; on failure `{found: false}` (same shape whether code or phone is wrong).
  - `modify_booking {party_size: int|null, start_iso: str|null}` — only the verified reservation; a new `start_iso` must be in `offered_starts`; `booking.modify(by_staff=False)`.
  - `cancel_booking {}` — only the verified reservation; signals `expect=confirm_cancel` (the cancel itself happens on the structured action).
  - `handoff_to_staff {reason: str}` — records a handoff event; voice → transfer number in the reply metadata; web → creates nothing, returns the restaurant's phone if configured.
  - `restaurant_info {}` — returns persona facts and today's service periods from rules.
- `async execute_tool(db, conv, name: str, tool_input: dict, *, now: datetime) -> ToolOutcome` where `ToolOutcome(result: dict, is_error: bool, expect: str|None)`; domain exceptions become `is_error=True` results with `{"error": code}`; never raises.

- [ ] **Step 1: Failing tests** (DB, `seeded_floor`-style fixture copied into `tests/reservations/agent/conftest.py`): find_slots records offered starts; hold of a non-offered time → `not_offered`; hold of an offered time → held reservation with diner fields and `expect == "confirm_hold"`; bad phone → `invalid_phone`; lookup with right code wrong phone → `{found: false}` and no verification; modify/cancel without verification → `not_verified`; cancel returns `expect == "confirm_cancel"` and does not cancel.
- [ ] **Step 2: Run** `... pytest tests/reservations/agent/test_tools.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(agent): reservation tools with offered-slot and verification guards (#259)`

---

### Task 3: AgentCore

**Files:** Create `agent/llm.py`, `agent/prompt.py`, `agent/core.py`, `tests/reservations/agent/test_core.py`.

**Interfaces:**
- `llm.LlmClient` Protocol: `async def create(*, system, messages, tools) -> LlmResponse` where `LlmResponse(content: list[dict], stop_reason: str, usage: dict)`; `AnthropicLlm` wraps `anthropic.AsyncAnthropic().messages.create(model=settings.RESERVATION_AGENT_MODEL, max_tokens=settings.RESERVATION_AGENT_MAX_TOKENS, system=..., messages=..., tools=TOOLS, tool_choice={"type":"auto"}, output_config={"effort":"low"}, cache_control={"type":"ephemeral"})` and converts blocks with `.model_dump()`; SDK errors (`anthropic.APIStatusError`, `APIConnectionError`) are caught by the core. `ScriptedLlm` test double in `tests/reservations/agent/fakes.py` returns queued responses and records requests.
- `prompt.build_system_prompt(location_name: str, timezone: str, rules: RulesSchema) -> str` — persona greeting/facts, policies (never invent availability; always call find_slots; collect name + phone before holding; tell the diner to press Confirm; keep replies under 2 short sentences on voice; English only; hand off for parties over the self-book limit or after two declined offers; never reveal other diners' details). No timestamps.
- `TurnInput(text: str | None, action: Literal["confirm_hold","decline_hold","confirm_cancel"] | None, channel: str)`; `TurnOutput(reply_text: str, expect: Literal["text","confirm_hold","confirm_cancel","phone_number","party_size","confirmation_code"], hold: dict | None, ended: bool, handoff: dict | None)`.
- `core.AgentCore(llm: LlmClient)` with `async handle_turn(db, conv: AgentConversation, turn: TurnInput, *, now: datetime | None = None) -> TurnOutput`:
  1. Turn cap → closing reply, `finish(outcome="abandoned")` if nothing booked.
  2. Structured actions bypass the model: `confirm_hold` → `booking.confirm` on `conv.held_reservation_id` with its stored diner → reply with the code ("You're booked … your code is ABC123") → `finish("booked")` or keep open for pending ("the restaurant will confirm"); `HoldExpired` → reply that the hold expired and ask to pick again (model continues next turn). `decline_hold` → `booking.cancel` the hold, reply asking for another time. `confirm_cancel` → `booking.cancel` verified reservation → `finish("cancelled")`.
  3. Free text → append a user message `"[{local now, ISO date, weekday}] {text}"`; run up to `MAX_TOOL_ROUNDS` model calls, executing every `tool_use` block via `execute_tool` and returning all `tool_result`s in one user message; record each call/result/usage; stop on `end_turn`.
  4. The reply text is the last assistant text; `expect` is the last tool's `expect` or `"text"`.
  5. `refusal`, `max_tokens`, SDK error or round cap → reply "Sorry, let me get someone to help" + handoff event, `finish("handed_off")` (web shows the restaurant's phone).
  - Message history for the model is rebuilt from `agent_events` each turn (append-only; assistant content blocks replayed exactly as returned, including any thinking blocks).

- [ ] **Step 1: Failing tests** with `ScriptedLlm`: full booking path (find_slots → hold → reply → `confirm_hold` action books, code in reply, outcome booked); free-text "yes" after a hold does NOT book (`confirm` not called, `expect == "confirm_hold"`); hold expired then `confirm_hold` → expired message, conversation open; refusal → handoff; SDK error → handoff, no exception; history rebuild replays assistant blocks byte-identical; system prompt identical across two turns (cache-stable); cost on conversation equals sum of events.
- [ ] **Step 2: Run** `... pytest tests/reservations/agent/test_core.py -q` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(agent): channel-agnostic AgentCore with structured confirmation (#259)`

---

### Task 4: Public web endpoints

**Files:** Create `backend/routers/public_reservations.py`, `backend/schemas/reservation_agent.py`, `tests/reservations/agent/test_public_api.py`; modify `backend/main.py`.

**Interfaces** (prefix `/api/v1/public/reservations`, no auth, 404 when flag off or company/location unknown/archived):
- `GET /{slug}/{location_id}` → `{location_name, timezone, greeting}`.
- `POST /{slug}/{location_id}/conversations` → 201 `{conversation_token, reply_text, expect}` (greeting turn, no model call).
- `POST /{slug}/{location_id}/conversations/{token}/turns` body `{text?: str (1..MAX_MESSAGE_CHARS), action?: "confirm_hold"|"decline_hold"|"confirm_cancel"}` (exactly one) → `TurnOutput` JSON. Unknown/closed token → 404. Per-IP `SlidingWindowLimiter(RESERVATION_AGENT_IP_TURNS_PER_HOUR, 3600)` → 429 before any model call. Turn cap enforced by the core.
- Tenant binding as in Global Constraints, done in one dependency `public_company_scope(slug, location_id, db)`.
- `AgentCore` instance from a dependency `get_agent_core()` returning `AgentCore(AnthropicLlm())`, overridden in tests with `ScriptedLlm`.

- [ ] **Step 1: Failing tests**: flag off → 404; unknown slug → 404; archived location → 404; start conversation returns token + greeting; turn round-trip with scripted LLM; both text and action → 422; text over 1000 chars → 422; 61st turn from one IP in an hour → 429 and the scripted LLM saw no call; another company's conversation token under this slug → 404.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `... pytest tests/reservations -q` — PASS.
- [ ] **Step 5: Commit** `feat(agent): public web chat endpoints (#259)`

---

### Task 5: Web chat page

**Files:** Create `frontend/src/api/publicReservations.ts`, `frontend/src/pages/ReserveChat.tsx`, `frontend/src/utils/chat.ts`, `frontend/src/utils/chat.test.ts`; modify `frontend/src/App.tsx` (public route `/r/:slug/:locationId`).

**Interfaces:**
- `utils/chat.ts`: `type ChatMessage = { from: "diner" | "host"; text: string }`; `reduceTurn(messages: ChatMessage[], sent: string | null, out: TurnOutput) -> ChatMessage[]`; `actionsFor(expect: string) -> Array<"confirm_hold" | "decline_hold" | "confirm_cancel">` (`confirm_hold` → `["confirm_hold","decline_hold"]`, `confirm_cancel` → `["confirm_cancel"]`, else `[]`).
- Page: header with the location name; message list; input box (disabled while sending; Enter sends; max 1000 chars); when `actionsFor(expect)` is non-empty, show buttons "Confirm booking" / "Pick another time" / "Yes, cancel it" that send the action; when `ended`, disable input and show the confirmation code prominently if present; on 429 show "Too many messages, try again later"; diner-facing copy English only (not in i18n by design, spec: English-only diner agent).

- [ ] **Step 1: Failing tests** for `reduceTurn` (appends diner then host; null `sent` appends host only) and `actionsFor`.
- [ ] **Step 2: Run** `cd frontend && npm test` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test`, `npx tsc --noEmit`, `npm run build` — PASS.
- [ ] **Step 5: Commit** `feat(agent): public web chat page (#259)`

---

### Task 6: Conversations view, retention, GDPR

**Files:** Create `frontend/src/pages/manager/Conversations.tsx`, `tests/reservations/agent/test_manager_conversations.py`, `tests/reservations/test_retention_gdpr.py`; modify `backend/routers/reservations.py`, `backend/schemas/reservations.py`, `backend/services/data_retention.py`, `backend/routers/gdpr.py` (or the service it calls), `frontend/src/api/reservations.ts`, nav (`/manager/reservations/conversations`, `requiresReservations`), App routes, 19 locale files.

**Interfaces:**
- `GET /api/v1/reservations/locations/{location_id}/conversations?day=YYYY-MM-DD&outcome=` → list `{id, channel, started_at, ended_at, outcome, turn_count, cost_usd, reservation_id}`; `GET /api/v1/reservations/conversations/{id}` → conversation + ordered events (payload, latency_ms, tokens, cost). Same auth/scope rules as the other manager routes.
- Retention: `settings.RETENTION_RESERVATIONS_DAYS = 365`; `run_data_retention` deletes conversations (events cascade) with `started_at` older than that, and clears diner name/phone/email/notes on reservations whose `end_at` is older than that (keeps the row for covers analytics). Counts added to the summary dict.
- GDPR company export includes reservations (with diner fields) and conversations for the company.
- Manager page: list filtered by day and outcome; click opens the transcript with user/assistant turns, tool calls collapsed, per-turn latency and cost; translated in 19 locales.

- [ ] **Step 1: Failing tests**: list/detail scoped (other company → 404, restricted manager out of scope → 404); retention deletes a 400-day-old conversation and scrubs an old reservation's diner fields, leaves a 10-day-old one; GDPR export contains a reservation's diner name.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `... pytest tests/reservations -q` + the retention/GDPR test files + `cd frontend && npm test && npx tsc --noEmit`, then the full backend suite once — PASS.
- [ ] **Step 5: Commit** `feat(agent): conversations view, retention and GDPR export (#259)`
