"""AgentCore (#259): the channel-agnostic turn handler.

One `handle_turn` call takes one diner input (free text or a structured
button action) and returns one reply. Free text runs a Claude tool-use loop
over `tools.TOOLS`; the structured actions (`confirm_hold`, `decline_hold`,
`confirm_cancel`, `confirm_modify`) never reach the model, so typed text
cannot book, change or cancel anything: only the button does. The tools
only hold or stage (`held_reservation_id`, `pending_cancel_id`,
`pending_modify`); the actions apply what is staged.

Everything is recorded as `agent_events`, and the model's message history is
rebuilt from them each turn (assistant blocks replayed exactly as returned,
thinking blocks included), so the system prompt and history prefix stay
byte-stable for the prompt cache. JSONB reorders keys, so message events also
keep the exact JSON text (`raw`) they replay from.

A turn has a deadline (RESERVATION_AGENT_TURN_DEADLINE_SECONDS, on
`self.clock`): no model call starts past it and each call's timeout is what
is left (at most LLM_TIMEOUT_S). Past it the conversation stays open and the
diner is asked to send the message again.

Turns are claimed by the caller first (`recording.claim_turn`: one atomic
UPDATE that counts the turn and marks it in flight); `handle_turn` clears
the in-flight mark with its last commit.

Transactions: `handle_turn` owns them and commits at every round boundary:
after the diner's message is recorded and after each tool batch, before the
next model call, so no row lock (a hold locks its location) is held across
model latency. A round's tool_use and tool_result events commit together.
On any unexpected error only the current round is rolled back; an `error`
event is recorded, the conversation is finished as `failed` and that is
committed. It never raises.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location
from backend.reservations.agent import recording, tools
from backend.reservations.agent.llm import LLM_TIMEOUT_S, LlmClient, LlmResponse
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.agent.prompt import build_system_prompt
from backend.reservations.providers import (
    BOOKED,
    HELD,
    PENDING,
    BookingProvider,
    HoldExpired,
    ProviderBooking,
    ProviderContext,
    ProviderError,
    Transient,
    provider_for,
)
from backend.reservations.rules import RulesSchema, get_rules

logger = logging.getLogger(__name__)

Action = Literal["confirm_hold", "decline_hold", "confirm_cancel", "confirm_modify"]
Expect = Literal["text", "confirm_hold", "confirm_cancel", "confirm_modify",
                 "phone_number", "party_size", "confirmation_code"]

HANDOFF_TEXT = "Sorry, let me get someone to help."  # voice: staff take the call
DEADLINE_TEXT = "Sorry, that took longer than expected — please send that again."
CLOSING_TEXT = ("We've been chatting for a while, so I'll end here. "
                "Please start a new conversation if you still need help.")
ENDED_TEXT = "This conversation has ended. Please start a new one if you need anything else."
FALLBACK_TEXT = "Sorry, could you say that again?"
# Prefixed to the reply when a provider call failed with Transient and its
# one retry (same turn, same idempotency key) succeeded.
RETRY_TEXT = "Sorry, I couldn't reach the booking system — let me try that again."
PROVIDER_DOWN = "provider_unavailable"  # the handoff reason after two Transients

_HISTORY_TYPES = ("user_turn", "assistant_turn", "tool_result", "action")
_TOOL_EXPECTS = {tools.EXPECT_CONFIRM_HOLD, tools.EXPECT_CONFIRM_CANCEL,
                 tools.EXPECT_CONFIRM_MODIFY}


@dataclass(frozen=True)
class TurnInput:
    """The channel is the conversation's (`conv.channel`), not the input's."""

    text: str | None
    action: Action | None


@dataclass(frozen=True)
class TurnOutput:
    reply_text: str
    expect: Expect
    hold: dict[str, Any] | None
    ended: bool
    handoff: dict[str, Any] | None
    # What a confirm_modify / confirm_cancel button applies, from the
    # verified booking and what is staged (never from the model's text).
    proposal: dict[str, Any] | None = None


def handoff_text(channel: str, phone: str | None) -> str:
    """On the phone the call is transferred; a web diner is told who to call."""
    if channel == "voice":
        return HANDOFF_TEXT
    if phone:
        return f"Please call the restaurant at {phone}."
    return "Please contact the restaurant directly."


def _slot(start: datetime, party_size: int, zone: ZoneInfo) -> dict[str, Any]:
    local = start.astimezone(zone)
    return {"local_date": local.date().isoformat(),
            "local_time": local.strftime("%H:%M"), "party_size": party_size}


class _Handoff(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _ProviderDown(Exception):
    """A provider write failed with Transient twice in one action."""


def _key(conv: AgentConversation, action: str) -> str:
    """The idempotency key of this turn's `action`: a retry in the same turn
    reuses it, so an external provider never acts twice."""
    return f"{conv.id}:{action}:{conv.turn_count or 0}"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _when(res: ProviderBooking, zone: ZoneInfo) -> str:
    local = res.start.astimezone(zone)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local:%A, %B} {local.day} at {hour}:{local:%M %p}"


async def _provider(db: AsyncSession, conv: AgentConversation,
                    now: datetime | None = None
                    ) -> tuple[BookingProvider, ProviderContext]:
    """The conversation's booking provider and its context. The location is
    loaded as handle_turn loads it (archived or not), so an action on a
    booking the diner already holds still applies. Writes pass the turn's
    `now`; a read may leave it to the wall clock."""
    now = now or datetime.now(timezone.utc)
    loc = (await db.execute(
        select(Location).where(Location.id == conv.location_id,
                               Location.company_id == conv.company_id)
    )).scalar_one()
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    return provider_for(conv), ProviderContext(
        company_id=conv.company_id, location_id=conv.location_id,
        timezone=loc.timezone, rules=rules, now=now.astimezone(timezone.utc),
        conversation_id=conv.id, seq=conv.turn_count or 0)


def _diner_text(raw: str | None) -> str:
    """The diner's text, capped, with leading brackets stripped so it cannot
    pose as a system context block."""
    text = (raw or "").strip()[:settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS]
    return text.lstrip("[ \t\r\n").strip()


class AgentCore:
    def __init__(self, llm: LlmClient,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.llm = llm
        self.clock = clock  # seconds, for the whole-turn deadline

    async def handle_turn(
        self, db: AsyncSession, conv: AgentConversation, turn: TurnInput,
        *, now: datetime | None = None,
    ) -> TurnOutput:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started = self.clock()
        conv_id, company_id, channel = conv.id, conv.company_id, conv.channel
        try:
            out = await self._turn(db, conv, turn, now, started)
            recording.end_turn(conv)
            await db.commit()
            return out
        except Exception:
            logger.exception("agent turn failed (conversation %s)", conv_id)
            return await self._fail(db, conv_id, company_id, channel, now)

    async def close_conversation(self, db: AsyncSession, conv: AgentConversation,
                                 *, now: datetime) -> None:
        """Close an open conversation outside a turn, as the turn cap does:
        release a live hold and finish with the same outcome (`booked`, a
        submitted pending request `handed_off`, otherwise `abandoned`).
        Commits. A conversation that is already closed is left as it is."""
        if conv.status != "open":
            return
        await self._release_hold(db, conv, now.astimezone(timezone.utc))
        await recording.finish(db, conv, await self._outcome(db, conv, "abandoned"))
        recording.end_turn(conv)
        await db.commit()

    # -- failure -----------------------------------------------------------

    async def _fail(self, db: AsyncSession, conv_id: str, company_id: str,
                    channel: str, now: datetime) -> TurnOutput:
        phone: str | None = None
        conv: AgentConversation | None = None
        try:
            await db.rollback()  # only the current round is uncommitted
            conv = (await db.execute(
                select(AgentConversation)
                .where(AgentConversation.id == conv_id,
                       AgentConversation.company_id == company_id)
                .execution_options(populate_existing=True)
            )).scalar_one_or_none()
            if conv is not None:
                rules, _ = await get_rules(db, conv.company_id, conv.location_id)
                phone = rules.staff_transfer_number
                await recording.record_event(db, conv, "error", {"error": "internal"})
                recording.end_turn(conv)
                if conv.status == "open":
                    await recording.finish(db, conv, "failed")
                await db.commit()
        except Exception:
            logger.exception("agent failure could not be recorded (conversation %s)",
                             conv_id)
            conv = None
            try:
                await db.rollback()
            except Exception:
                logger.exception("rollback failed (conversation %s)", conv_id)
        if conv is not None:
            # Best effort: the conversation is over, so a hold it made earlier
            # (a round that committed before the failure) must not block the
            # tables until it lapses.
            try:
                if conv.held_reservation_id is not None \
                        and await self._release_hold(db, conv, now):
                    await db.commit()
            except Exception:
                logger.exception("could not release the hold (conversation %s)", conv_id)
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("rollback failed (conversation %s)", conv_id)
        return TurnOutput(handoff_text(channel, phone), "text", None, True,
                          {"reason": "error", "phone": phone})

    # -- the turn ----------------------------------------------------------

    async def _turn(self, db: AsyncSession, conv: AgentConversation,
                    turn: TurnInput, now: datetime, started: float) -> TurnOutput:
        if conv.status != "open":
            return TurnOutput(ENDED_TEXT, "text", None, True, None)
        # The caller claimed this turn (recording.claim_turn), which counted
        # it atomically; the claim past the cap is the closing turn. A button
        # press still runs on it (a Confirm pressed on the closing turn
        # books), and the conversation closes after it.
        closing = (conv.turn_count or 0) > settings.RESERVATION_AGENT_MAX_TURNS
        if closing and turn.action is None:
            return await self._close_at_cap(db, conv, now, None)

        loc = (await db.execute(
            select(Location).where(Location.id == conv.location_id,
                                   Location.company_id == conv.company_id)
        )).scalar_one()
        zone = ZoneInfo(loc.timezone)
        rules, _ = await get_rules(db, conv.company_id, conv.location_id)

        if turn.action is not None:
            out = await self._action(db, conv, turn.action, now, zone)
            if closing and conv.status == "open":
                return await self._close_at_cap(db, conv, now, out.reply_text)
            return out

        text = _diner_text(turn.text)
        if not text:
            return await self._output(db, conv, FALLBACK_TEXT, None, zone)
        try:
            return await self._model_turn(db, conv, text, now, zone, loc.name, rules,
                                          started)
        except _Handoff as h:
            return await self._handoff(db, conv, h.reason, now, rules)

    async def _close_at_cap(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime, reply: str | None) -> TurnOutput:
        await self._release_hold(db, conv, now)
        await recording.finish(db, conv, await self._outcome(db, conv, "abandoned"))
        text = f"{reply} {CLOSING_TEXT}" if reply else CLOSING_TEXT
        return TurnOutput(text, "text", None, True, None)

    async def _model_turn(self, db: AsyncSession, conv: AgentConversation, text: str,
                          now: datetime, zone: ZoneInfo, location_name: str,
                          rules: RulesSchema, started_at: float) -> TurnOutput:
        system = build_system_prompt(location_name, zone.key, rules, conv.channel)
        messages = await self._history(db, conv)
        local = now.astimezone(zone)
        # The context line is its own block, so diner text cannot extend it.
        content = [
            {"type": "text",
             "text": f"[Now: {local.date().isoformat()} {local:%H:%M}, {local:%A}]"},
            {"type": "text", "text": text},
        ]
        await recording.record_event(db, conv, "user_turn",
                                     {"content": content, "raw": json.dumps(content)})
        _append(messages, "user", content)
        recording.touch_turn(conv)
        await db.commit()

        rounds = settings.RESERVATION_AGENT_MAX_TOOL_ROUNDS
        last_expect: str | None = None
        handoff: dict[str, Any] | None = None
        response: LlmResponse | None = None
        retried = False  # a tool succeeded on its retry after a Transient
        for round_no in range(rounds):
            # The whole turn has a deadline: no model call starts past it,
            # and none may run beyond it.
            remaining = (settings.RESERVATION_AGENT_TURN_DEADLINE_SECONDS
                         - (self.clock() - started_at))
            if remaining <= 0:
                return await self._past_deadline(db, conv, "turn_deadline", now, zone,
                                                 rules, handoff)
            started = time.perf_counter()
            try:
                # The SDK's own timeout is per attempt and it retries, so
                # bound the whole call (retries included) by what is left.
                response = await asyncio.wait_for(
                    self.llm.create(system=system, messages=messages,
                                    tools=tools.TOOLS,
                                    timeout=min(LLM_TIMEOUT_S, remaining)),
                    timeout=remaining)
            except (anthropic.APITimeoutError, asyncio.TimeoutError):
                logger.warning("agent model call timed out (conversation %s)", conv.id)
                return await self._past_deadline(db, conv, "llm_timeout", now, zone,
                                                 rules, handoff,
                                                 latency_ms={"llm": _ms(started)})
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                logger.warning("agent model call failed (conversation %s): %s",
                               conv.id, exc)
                await recording.record_event(
                    db, conv, "error", {"error": "llm_error", "detail": type(exc).__name__},
                    latency_ms={"llm": _ms(started)})
                raise _Handoff("llm_error") from exc
            await recording.record_event(
                db, conv, "assistant_turn",
                {"content": response.content, "raw": json.dumps(response.content),
                 "stop_reason": response.stop_reason},
                latency_ms={"llm": _ms(started)}, usage=response.usage)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break
            uses = [b for b in response.content if b.get("type") == "tool_use"]
            if response.stop_reason != "tool_use" or not uses:
                raise _Handoff(response.stop_reason)
            if round_no == rounds - 1:
                # No model call is left to read the results: don't act on them.
                raise _Handoff("round_cap")

            started = time.perf_counter()
            results: list[dict[str, Any]] = []
            lockout: str | None = None
            for block in uses:
                name, tool_input = block.get("name"), block.get("input")
                await recording.record_event(db, conv, "tool_call", {
                    "tool_use_id": block.get("id"), "name": name, "input": tool_input})
                outcome = await tools.execute_tool(
                    db, conv, str(name), tool_input if isinstance(tool_input, dict) else {},
                    now=now)
                # Only a successful tool may raise a confirm button.
                if not outcome.is_error:
                    last_expect = outcome.expect
                    retried = retried or outcome.retried
                    if outcome.expect == tools.EXPECT_HANDOFF:
                        reason = (tool_input or {}).get("reason", "")
                        handoff = {"reason": reason if isinstance(reason, str) else "",
                                   "phone": rules.staff_transfer_number}
                elif outcome.result.get("handoff") is True:
                    # The phone-wide lookup lockout, or a provider that failed
                    # twice: staff take over.
                    lockout = (PROVIDER_DOWN if outcome.result.get("error") == PROVIDER_DOWN
                               else "lookup_lockout")
                result = {"type": "tool_result", "tool_use_id": block.get("id"),
                          "content": json.dumps(outcome.result)}
                if outcome.is_error:
                    result["is_error"] = True
                results.append(result)
            await recording.record_event(db, conv, "tool_result",
                                         {"content": results, "raw": json.dumps(results)},
                                         latency_ms={"tools": _ms(started)})
            messages.append({"role": "user", "content": results})
            if lockout:
                raise _Handoff(lockout)
            # Release the round's locks (a hold locks its location) before
            # the next model call.
            recording.touch_turn(conv)
            await db.commit()

        assert response is not None
        texts = [b.get("text", "") for b in response.content
                 if b.get("type") == "text" and b.get("text")]
        reply = texts[-1] if texts else FALLBACK_TEXT
        if retried:
            reply = f"{RETRY_TEXT} {reply}"
        if handoff is not None:
            # handoff_to_staff already recorded its `handoff` event.
            await self._release_hold(db, conv, now)
            await recording.finish(db, conv, "handed_off")
            return TurnOutput(reply, "text", None, True, handoff)
        expect = last_expect if last_expect in _TOOL_EXPECTS else None
        return await self._output(db, conv, reply, expect, zone)

    async def _past_deadline(self, db: AsyncSession, conv: AgentConversation,
                             error: str, now: datetime, zone: ZoneInfo,
                             rules: RulesSchema, handoff: dict[str, Any] | None,
                             latency_ms: dict[str, Any] | None = None) -> TurnOutput:
        """The turn ran out of time. The conversation stays open and nothing
        is released (a hold keeps its Confirm button); the diner sends again.
        Everything up to the last round boundary is already committed, and the
        history stays valid (it ends with the diner's message or a tool
        result, which the next turn's message joins)."""
        await recording.record_event(db, conv, "error", {"error": error},
                                     latency_ms=latency_ms)
        if handoff is not None:
            # handoff_to_staff already ran in an earlier round: honour it.
            await self._release_hold(db, conv, now)
            await recording.finish(db, conv, "handed_off")
            return TurnOutput(handoff_text(conv.channel, rules.staff_transfer_number),
                              "text", None, True, handoff)
        return await self._output(db, conv, DEADLINE_TEXT, None, zone)

    async def _handoff(self, db: AsyncSession, conv: AgentConversation, reason: str,
                       now: datetime, rules: RulesSchema) -> TurnOutput:
        await recording.record_event(db, conv, "handoff", {"reason": reason})
        await self._release_hold(db, conv, now)
        await recording.finish(db, conv, "handed_off")
        phone = rules.staff_transfer_number
        text = handoff_text(conv.channel, phone)
        if reason == PROVIDER_DOWN:
            # The diner hears the retry sentence the first Transient earned,
            # then the handoff the second one did.
            text = f"{RETRY_TEXT} {text}"
        return TurnOutput(text, "text", None, True,
                          {"reason": reason, "phone": phone})

    # -- structured actions ------------------------------------------------

    async def _action(self, db: AsyncSession, conv: AgentConversation, action: str,
                      now: datetime, zone: ZoneInfo) -> TurnOutput:
        handlers = {"confirm_hold": self._confirm_hold,
                    "decline_hold": self._decline_hold,
                    "confirm_cancel": self._confirm_cancel,
                    "confirm_modify": self._confirm_modify}
        handler = handlers.get(action)
        if handler is None:
            out, result = await self._output(db, conv, FALLBACK_TEXT, None, zone), \
                "unknown_action"
        else:
            try:
                out, result = await handler(db, conv, now, zone)
            except _ProviderDown:
                rules, _ = await get_rules(db, conv.company_id, conv.location_id)
                out, result = await self._handoff(db, conv, PROVIDER_DOWN, now,
                                                  rules), PROVIDER_DOWN
        await recording.record_event(db, conv, "action", {
            "action": action, "result": result, "reply": out.reply_text})
        return out

    async def _write(self, db: AsyncSession, conv: AgentConversation,
                     ctx: ProviderContext, action: str,
                     call: Callable[[ProviderContext], Awaitable[ProviderBooking]]
                     ) -> tuple[ProviderBooking, bool]:
        """Run one provider write in a savepoint; on Transient, retry it once
        (same turn, so the same idempotency key) with `attempt=1`. Returns the
        booking and whether it took the retry; raises `_ProviderDown` after a
        second Transient. Any other ProviderError propagates."""
        for attempt in (0, 1):
            try:
                async with db.begin_nested():
                    return await call(replace(ctx, attempt=attempt)), attempt == 1
            except Transient:
                logger.warning("agent %s: provider unavailable (conversation %s, "
                               "attempt %d)", action, conv.id, attempt)
                await recording.record_event(db, conv, "error", {
                    "error": PROVIDER_DOWN, "action": action, "attempt": attempt})
        raise _ProviderDown()

    @staticmethod
    def _retried(out: TurnOutput, retried: bool) -> TurnOutput:
        return replace(out, reply_text=f"{RETRY_TEXT} {out.reply_text}") if retried else out

    async def _confirm_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        if conv.held_reservation_id is None:
            return (await self._output(
                db, conv, "There's nothing to confirm yet. What date, time and party "
                "size would you like?", None, zone), "nothing_held")
        provider, ctx = await _provider(db, conv, now)
        ref, key = conv.held_reservation_id, _key(conv, "book")
        try:
            res, retried = await self._write(
                db, conv, ctx, "book",
                lambda c: provider.book(db, c, ref, idempotency_key=key))
        except HoldExpired:
            conv.held_reservation_id = None
            await db.flush()
            return (await self._output(
                db, conv, "Sorry, that hold expired before it was confirmed. Shall I "
                "look for another time?", None, zone), "hold_expired")
        except ProviderError as exc:
            conv.held_reservation_id = None
            await db.flush()
            return (await self._output(
                db, conv, "Sorry, that time is no longer held. Shall I look for "
                "another time?", None, zone), exc.code)
        conv.held_reservation_id = None
        conv.reservation_id = res.ref
        await db.flush()
        when = _when(res, zone)
        if res.status == BOOKED:
            await recording.finish(db, conv, "booked")
            local = res.start.astimezone(zone)
            booked = {"party_size": res.party_size,
                      "local_date": local.date().isoformat(),
                      "local_time": local.strftime("%H:%M"),
                      "start_iso": local.isoformat(), "status": "booked",
                      "held_until": None,
                      "confirmation_code": res.code}
            return (self._retried(TurnOutput(
                f"You're booked for {res.party_size} on {when}. Your confirmation "
                f"code is {res.code}.", "text", booked, True, None), retried),
                f"booked {res.code}")
        return (self._retried(TurnOutput(
            f"Thanks, your request for {res.party_size} on {when} is in, and the "
            f"restaurant will confirm it. Your reference code is "
            f"{res.code}.", "text", None, False, None), retried),
            f"pending_approval {res.code}")

    async def _decline_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        """The diner's "No": releases a hold and drops a staged change or cancel."""
        released = await self._release_hold(db, conv, now)
        staged = conv.pending_modify is not None or conv.pending_cancel_id is not None
        conv.held_reservation_id = None
        conv.pending_modify = None
        conv.pending_cancel_id = None
        await db.flush()
        if released:
            return (await self._output(
                db, conv, "No problem, I've let that time go. What other time would "
                "work?", None, zone), "released")
        if staged:
            return (await self._output(
                db, conv, "OK, I've left your booking as it was. Anything else?",
                None, zone), "kept")
        return (await self._output(
            db, conv, "There's nothing waiting for confirmation. What would you like "
            "to do?", None, zone), "nothing_held")

    async def _staged(self, db: AsyncSession, conv: AgentConversation,
                      res_id: str | None,
                      now: datetime | None = None) -> ProviderBooking | None:
        """A staged target, re-validated: still the verified booking, this
        company and location, and still booked or pending."""
        if res_id is None or res_id != conv.verified_reservation_id:
            return None
        provider, ctx = await _provider(db, conv, now)
        res = await provider.lookup(db, ctx, ref=res_id)
        if res is None or res.status not in (BOOKED, PENDING):
            return None
        return res

    async def _confirm_cancel(self, db: AsyncSession, conv: AgentConversation,
                              now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        res = await self._staged(db, conv, conv.pending_cancel_id, now)
        conv.pending_cancel_id = None
        await db.flush()
        if res is None:
            return (await self._output(
                db, conv, "There's no cancellation waiting to be confirmed. Which "
                "booking would you like to cancel?", None, zone), "nothing_pending")
        provider, ctx = await _provider(db, conv, now)
        ref, key = res.ref, _key(conv, "cancel")
        try:
            res, retried = await self._write(
                db, conv, ctx, "cancel",
                lambda c: provider.cancel(db, c, ref, idempotency_key=key))
        except ProviderError as exc:
            return (await self._output(
                db, conv, "Sorry, that booking can't be cancelled here. Please contact "
                "the restaurant.", None, zone), exc.code)
        conv.reservation_id = res.ref
        conv.verified_reservation_id = None
        conv.pending_modify = None
        await db.flush()
        await recording.finish(db, conv, "cancelled")
        return (self._retried(TurnOutput(
            f"Your booking {res.code} for {_when(res, zone)} is cancelled.",
            "text", None, True, None), retried), "cancelled")

    async def _confirm_modify(self, db: AsyncSession, conv: AgentConversation,
                              now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        staged = conv.pending_modify
        res = await self._staged(db, conv, conv.verified_reservation_id, now) \
            if isinstance(staged, dict) else None
        conv.pending_modify = None
        await db.flush()
        if res is None or not isinstance(staged, dict):
            return (await self._output(
                db, conv, "There's no change waiting to be confirmed. What would you "
                "like to change?", None, zone), "nothing_pending")
        try:
            start_iso, party = staged.get("start_iso"), staged.get("party_size")
            start = datetime.fromisoformat(start_iso) if isinstance(start_iso, str) else None
            provider, ctx = await _provider(db, conv, now)
            ref, key = res.ref, _key(conv, "modify")
            new_party = party if isinstance(party, int) else None
            res, retried = await self._write(
                db, conv, ctx, "modify",
                lambda c: provider.modify(db, c, ref, party_size=new_party, start=start,
                                          idempotency_key=key))
        except (ProviderError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_input")
            return (await self._output(
                db, conv, "Sorry, I couldn't make that change, so your booking is "
                "unchanged. Shall I look for another option?", None, zone), code)
        conv.reservation_id = res.ref
        conv.verified_reservation_id = None
        conv.pending_cancel_id = None
        await db.flush()
        await recording.finish(db, conv, "modified")
        return (self._retried(TurnOutput(
            f"Done: booking {res.code} is now for {res.party_size} on "
            f"{_when(res, zone)}.", "text", None, True, None), retried), "modified")

    # -- helpers -----------------------------------------------------------

    async def _outcome(self, db: AsyncSession, conv: AgentConversation,
                       default: str) -> str:
        """`booked` only for a booked reservation; a submitted pending request
        is with staff, so `handed_off`."""
        if conv.reservation_id is None:
            return default
        provider, ctx = await _provider(db, conv)
        res = await provider.lookup(db, ctx, ref=conv.reservation_id)
        status = res.status if res is not None else None
        if status == BOOKED:
            return "booked"
        if status == PENDING:
            return "handed_off"
        return default

    async def _active_hold(self, db: AsyncSession, conv: AgentConversation,
                           now: datetime | None = None) -> ProviderBooking | None:
        """The conversation's unconfirmed hold, if it is still live."""
        if conv.held_reservation_id is None:
            return None
        provider, ctx = await _provider(db, conv, now)
        res = await provider.lookup(db, ctx, ref=conv.held_reservation_id)
        if res is None or res.status not in (HELD, PENDING) or res.code is not None:
            return None
        return res

    async def _release_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime) -> bool:
        """Cancel the conversation's unconfirmed hold. Never a confirmed row."""
        res = await self._active_hold(db, conv, now)
        if res is None:
            return False
        provider, ctx = await _provider(db, conv, now)
        try:
            async with db.begin_nested():
                await provider.cancel(db, ctx, res.ref)
        except ProviderError:
            return False  # lapsed under the lock: nothing to release
        return True

    async def _output(self, db: AsyncSession, conv: AgentConversation, reply: str,
                      expect: str | None, zone: ZoneInfo) -> TurnOutput:
        """An open-conversation reply. Whatever is still waiting for the diner's
        button (a live hold, a staged change or cancel) keeps that button up."""
        res = await self._active_hold(db, conv)
        hold = None
        if res is not None:
            local = res.start.astimezone(zone)
            hold = {"hold_id": res.ref, "party_size": res.party_size,
                    "local_date": local.date().isoformat(),
                    "local_time": local.strftime("%H:%M"),
                    "start_iso": local.isoformat(),
                    "status": "pending_approval" if res.status == PENDING else "held",
                    "held_until": res.held_until.isoformat() if res.held_until else None}
        if expect is None:
            if hold is not None:
                expect = tools.EXPECT_CONFIRM_HOLD
            elif conv.pending_modify is not None:
                expect = tools.EXPECT_CONFIRM_MODIFY
            elif conv.pending_cancel_id is not None:
                expect = tools.EXPECT_CONFIRM_CANCEL
            else:
                expect = "text"
        proposal = await self._proposal(db, conv, expect, zone)
        return TurnOutput(reply, expect, hold, False, None,  # type: ignore[arg-type]
                          proposal)

    async def _proposal(self, db: AsyncSession, conv: AgentConversation,
                        expect: str, zone: ZoneInfo) -> dict[str, Any] | None:
        """What the confirm button would apply, built from the verified
        booking and the staged change, in the location's local time."""
        if expect == tools.EXPECT_CONFIRM_CANCEL:
            res = await self._staged(db, conv, conv.pending_cancel_id)
            if res is None:
                return None
            return {"kind": "cancel", "current": _slot(res.start, res.party_size, zone),
                    "proposed": None}
        if expect != tools.EXPECT_CONFIRM_MODIFY or not isinstance(conv.pending_modify,
                                                                   dict):
            return None
        res = await self._staged(db, conv, conv.verified_reservation_id)
        if res is None:
            return None
        start_iso = conv.pending_modify.get("start_iso")
        party = conv.pending_modify.get("party_size")
        try:
            start = (datetime.fromisoformat(start_iso) if isinstance(start_iso, str)
                     else res.start)
        except ValueError:
            return None
        return {"kind": "modify", "current": _slot(res.start, res.party_size, zone),
                "proposed": _slot(start, party if isinstance(party, int) else
                                  res.party_size, zone)}

    async def _history(self, db: AsyncSession,
                       conv: AgentConversation) -> list[dict[str, Any]]:
        """The model's messages, rebuilt from the event log."""
        events = (await db.execute(
            select(AgentEvent.type, AgentEvent.payload)
            .where(AgentEvent.conversation_id == conv.id,
                   AgentEvent.company_id == conv.company_id,
                   AgentEvent.type.in_(_HISTORY_TYPES))
            .order_by(AgentEvent.seq)
        )).all()
        messages: list[dict[str, Any]] = []
        for type_, payload in events:
            if type_ == "user_turn":
                _append(messages, "user", _raw(payload))
            elif type_ == "assistant_turn":
                messages.append({"role": "assistant", "content": _raw(payload)})
            elif type_ == "tool_result":
                _append(messages, "user", _raw(payload))
            elif type_ == "action":
                # A system note in its own block, then the core's reply.
                _append(messages, "user", [{"type": "text", "text":
                        f"[The diner pressed {payload['action']}: {payload['result']}]"}])
                _append(messages, "assistant",
                        [{"type": "text", "text": payload["reply"]}])
        return messages


def _raw(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocks as originally sent. JSONB reorders object keys, so the exact
    JSON text is kept in `raw` and replayed from it, byte for byte."""
    raw = payload.get("raw")
    return json.loads(raw) if isinstance(raw, str) else payload["content"]


def _append(messages: list[dict[str, Any]], role: str,
            content: list[dict[str, Any]]) -> None:
    """Append a message, merging into the previous one if it has the same role
    (e.g. a typed turn right after an action note)."""
    if messages and messages[-1]["role"] == role:
        prev = messages[-1]
        messages[-1] = {"role": role, "content": list(prev["content"]) + list(content)}
    else:
        messages.append({"role": role, "content": content})
