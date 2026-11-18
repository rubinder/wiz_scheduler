"""AgentCore (#259): the channel-agnostic turn handler.

One `handle_turn` call takes one diner input (free text or a structured
button action) and returns one reply. Free text runs a Claude tool-use loop
over `tools.TOOLS`; the structured actions (`confirm_hold`, `decline_hold`,
`confirm_cancel`) never reach the model, so a typed "yes" cannot book or
cancel anything: only the button does.

Everything is recorded as `agent_events`, and the model's message history is
rebuilt from them each turn (assistant blocks replayed exactly as returned,
thinking blocks included), so the system prompt and history prefix stay
byte-stable for the prompt cache. JSONB reorders keys, so assistant and
tool_result events also keep the exact JSON text (`raw`) they replay from.

Transactions: `handle_turn` owns the turn's transaction. The turn runs in a
savepoint and `handle_turn` commits at the end; on any unexpected error the
savepoint is rolled back, an `error` event is recorded, the conversation is
finished as `failed` and that is committed. It never raises.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location
from backend.reservations import booking
from backend.reservations.agent import recording, tools
from backend.reservations.agent.llm import LlmClient, LlmResponse
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.agent.prompt import build_system_prompt
from backend.reservations.booking import HoldExpired, ReservationError
from backend.reservations.models import BOOKED, HELD, PENDING, Reservation
from backend.reservations.rules import RulesSchema, get_rules

logger = logging.getLogger(__name__)

Action = Literal["confirm_hold", "decline_hold", "confirm_cancel"]
Expect = Literal["text", "confirm_hold", "confirm_cancel", "phone_number",
                 "party_size", "confirmation_code"]

HANDOFF_TEXT = "Sorry, let me get someone to help."
CLOSING_TEXT = ("We've been chatting for a while, so I'll end here. "
                "Please start a new conversation if you still need help.")
ENDED_TEXT = "This conversation has ended. Please start a new one if you need anything else."
FALLBACK_TEXT = "Sorry, could you say that again?"

_HISTORY_TYPES = ("user_turn", "assistant_turn", "tool_result", "action")
_TOOL_EXPECTS = {tools.EXPECT_CONFIRM_HOLD, tools.EXPECT_CONFIRM_CANCEL}


@dataclass(frozen=True)
class TurnInput:
    text: str | None
    action: Action | None
    channel: str


@dataclass(frozen=True)
class TurnOutput:
    reply_text: str
    expect: Expect
    hold: dict[str, Any] | None
    ended: bool
    handoff: dict[str, Any] | None


class _Handoff(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _when(res: Reservation, zone: ZoneInfo) -> str:
    local = res.start_at.astimezone(zone)
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local:%A, %B} {local.day} at {hour}:{local:%M %p}"


class AgentCore:
    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    async def handle_turn(
        self, db: AsyncSession, conv: AgentConversation, turn: TurnInput,
        *, now: datetime | None = None,
    ) -> TurnOutput:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        conv_id, company_id = conv.id, conv.company_id
        try:
            async with db.begin_nested():
                out = await self._turn(db, conv, turn, now)
        except Exception:
            logger.exception("agent turn failed (conversation %s)", conv_id)
            return await self._fail(db, conv_id, company_id, rollback=False)
        try:
            await db.commit()
        except Exception:
            logger.exception("agent turn commit failed (conversation %s)", conv_id)
            return await self._fail(db, conv_id, company_id, rollback=True)
        return out

    # -- failure -----------------------------------------------------------

    async def _fail(self, db: AsyncSession, conv_id: str, company_id: str,
                    *, rollback: bool) -> TurnOutput:
        phone: str | None = None
        try:
            if rollback:
                await db.rollback()
            # The rolled-back savepoint expired the conversation: reload it.
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
                if conv.status == "open":
                    await recording.finish(db, conv, "failed")
                await db.commit()
        except Exception:
            logger.exception("agent failure could not be recorded (conversation %s)",
                             conv_id)
            try:
                await db.rollback()
            except Exception:
                logger.exception("rollback failed (conversation %s)", conv_id)
        return TurnOutput(HANDOFF_TEXT, "text", None, True,
                          {"reason": "error", "phone": phone})

    # -- the turn ----------------------------------------------------------

    async def _turn(self, db: AsyncSession, conv: AgentConversation,
                    turn: TurnInput, now: datetime) -> TurnOutput:
        if conv.status != "open":
            return TurnOutput(ENDED_TEXT, "text", None, True, None)
        if (conv.turn_count or 0) >= settings.RESERVATION_AGENT_MAX_TURNS:
            await self._release_hold(db, conv, now)
            await recording.finish(db, conv, "booked" if conv.reservation_id else "abandoned")
            return TurnOutput(CLOSING_TEXT, "text", None, True, None)
        conv.turn_count = (conv.turn_count or 0) + 1
        await db.flush()

        loc = (await db.execute(
            select(Location).where(Location.id == conv.location_id,
                                   Location.company_id == conv.company_id)
        )).scalar_one()
        zone = ZoneInfo(loc.timezone)
        rules, _ = await get_rules(db, conv.company_id, conv.location_id)

        if turn.action is not None:
            return await self._action(db, conv, turn.action, now, zone, rules)

        text = (turn.text or "").strip()[:settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS]
        if not text:
            return await self._output(db, conv, FALLBACK_TEXT, None, zone)
        try:
            return await self._model_turn(db, conv, text, now, zone, loc.name, rules)
        except _Handoff as h:
            return await self._handoff(db, conv, h.reason, now, rules)

    async def _model_turn(self, db: AsyncSession, conv: AgentConversation, text: str,
                          now: datetime, zone: ZoneInfo, location_name: str,
                          rules: RulesSchema) -> TurnOutput:
        system = build_system_prompt(location_name, zone.key, rules)
        messages = await self._history(db, conv)
        local = now.astimezone(zone)
        content = f"[Now: {local.date().isoformat()} {local:%H:%M}, {local:%A}] {text}"
        await recording.record_event(db, conv, "user_turn", {"content": content})
        _append(messages, "user", content)

        last_expect: str | None = None
        handoff: dict[str, Any] | None = None
        response: LlmResponse | None = None
        for _ in range(settings.RESERVATION_AGENT_MAX_TOOL_ROUNDS):
            started = time.perf_counter()
            try:
                response = await self.llm.create(system=system, messages=messages,
                                                 tools=tools.TOOLS)
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

            started = time.perf_counter()
            results: list[dict[str, Any]] = []
            for block in uses:
                name, tool_input = block.get("name"), block.get("input")
                await recording.record_event(db, conv, "tool_call", {
                    "tool_use_id": block.get("id"), "name": name, "input": tool_input})
                outcome = await tools.execute_tool(
                    db, conv, str(name), tool_input if isinstance(tool_input, dict) else {},
                    now=now)
                last_expect = outcome.expect
                if outcome.expect == tools.EXPECT_HANDOFF:
                    handoff = {"reason": (tool_input or {}).get("reason", ""),
                               "phone": rules.staff_transfer_number}
                result = {"type": "tool_result", "tool_use_id": block.get("id"),
                          "content": json.dumps(outcome.result)}
                if outcome.is_error:
                    result["is_error"] = True
                results.append(result)
            await recording.record_event(db, conv, "tool_result",
                                         {"content": results, "raw": json.dumps(results)},
                                         latency_ms={"tools": _ms(started)})
            messages.append({"role": "user", "content": results})
        else:
            raise _Handoff("round_cap")

        assert response is not None
        texts = [b.get("text", "") for b in response.content
                 if b.get("type") == "text" and b.get("text")]
        reply = texts[-1] if texts else FALLBACK_TEXT
        if handoff is not None:
            # handoff_to_staff already recorded its `handoff` event.
            await self._release_hold(db, conv, now)
            await recording.finish(db, conv, "handed_off")
            return TurnOutput(reply, "text", None, True, handoff)
        expect = last_expect if last_expect in _TOOL_EXPECTS else None
        return await self._output(db, conv, reply, expect, zone)

    async def _handoff(self, db: AsyncSession, conv: AgentConversation, reason: str,
                       now: datetime, rules: RulesSchema) -> TurnOutput:
        await recording.record_event(db, conv, "handoff", {"reason": reason})
        await self._release_hold(db, conv, now)
        await recording.finish(db, conv, "handed_off")
        return TurnOutput(HANDOFF_TEXT, "text", None, True,
                          {"reason": reason, "phone": rules.staff_transfer_number})

    # -- structured actions ------------------------------------------------

    async def _action(self, db: AsyncSession, conv: AgentConversation, action: Action,
                      now: datetime, zone: ZoneInfo, rules: RulesSchema) -> TurnOutput:
        if action == "confirm_hold":
            out, result = await self._confirm_hold(db, conv, now, zone)
        elif action == "decline_hold":
            out, result = await self._decline_hold(db, conv, now, zone)
        elif action == "confirm_cancel":
            out, result = await self._confirm_cancel(db, conv, now, zone)
        else:
            out = await self._output(db, conv, FALLBACK_TEXT, None, zone)
            result = "unknown_action"
        await recording.record_event(db, conv, "action", {
            "action": action, "result": result, "reply": out.reply_text})
        return out

    async def _confirm_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        if conv.held_reservation_id is None:
            return (await self._output(
                db, conv, "There's nothing to confirm yet. What date, time and party "
                "size would you like?", None, zone), "nothing_held")
        try:
            async with db.begin_nested():
                res = await booking.confirm(db, conv.company_id, conv.held_reservation_id,
                                            now=now)
        except HoldExpired:
            conv.held_reservation_id = None
            await db.flush()
            return (await self._output(
                db, conv, "Sorry, that hold expired before it was confirmed. Shall I "
                "look for another time?", None, zone), "hold_expired")
        except ReservationError as exc:
            conv.held_reservation_id = None
            await db.flush()
            return (await self._output(
                db, conv, "Sorry, that time is no longer held. Shall I look for "
                "another time?", None, zone), exc.code)
        conv.held_reservation_id = None
        conv.reservation_id = res.id
        await db.flush()
        when = _when(res, zone)
        if res.status == BOOKED:
            await recording.finish(db, conv, "booked")
            return (TurnOutput(
                f"You're booked for {res.party_size} on {when}. Your confirmation "
                f"code is {res.confirmation_code}.", "text", None, True, None),
                f"booked {res.confirmation_code}")
        return (TurnOutput(
            f"Thanks, your request for {res.party_size} on {when} is in, and the "
            f"restaurant will confirm it. Your reference code is "
            f"{res.confirmation_code}.", "text", None, False, None),
            f"pending_approval {res.confirmation_code}")

    async def _decline_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        released = await self._release_hold(db, conv, now)
        conv.held_reservation_id = None
        await db.flush()
        if not released:
            return (await self._output(
                db, conv, "There's no time on hold. What would you like to book?",
                None, zone), "nothing_held")
        return (await self._output(
            db, conv, "No problem, I've let that time go. What other time would work?",
            None, zone), "released")

    async def _confirm_cancel(self, db: AsyncSession, conv: AgentConversation,
                              now: datetime, zone: ZoneInfo) -> tuple[TurnOutput, str]:
        if conv.verified_reservation_id is None:
            return (TurnOutput(
                "I need to find your booking first. What's your confirmation code?",
                "confirmation_code", None, False, None), "not_verified")
        try:
            async with db.begin_nested():
                res = await booking.cancel(db, conv.company_id,
                                           conv.verified_reservation_id, now=now)
        except ReservationError as exc:
            return (await self._output(
                db, conv, "Sorry, that booking can't be cancelled here. Please contact "
                "the restaurant.", None, zone), exc.code)
        conv.reservation_id = res.id
        await db.flush()
        await recording.finish(db, conv, "cancelled")
        return (TurnOutput(
            f"Your booking {res.confirmation_code} for {_when(res, zone)} is cancelled.",
            "text", None, True, None), "cancelled")

    # -- helpers -----------------------------------------------------------

    async def _active_hold(self, db: AsyncSession,
                           conv: AgentConversation) -> Reservation | None:
        """The conversation's unconfirmed hold, if it is still live."""
        if conv.held_reservation_id is None:
            return None
        res = (await db.execute(
            select(Reservation).where(Reservation.id == conv.held_reservation_id,
                                      Reservation.company_id == conv.company_id)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if res is None or res.status not in (HELD, PENDING) \
                or res.confirmation_code is not None:
            return None
        return res

    async def _release_hold(self, db: AsyncSession, conv: AgentConversation,
                            now: datetime) -> bool:
        """Cancel the conversation's unconfirmed hold. Never a confirmed row."""
        res = await self._active_hold(db, conv)
        if res is None:
            return False
        try:
            async with db.begin_nested():
                await booking.cancel(db, conv.company_id, res.id, now=now)
        except ReservationError:
            return False  # lapsed under the lock: nothing to release
        return True

    async def _output(self, db: AsyncSession, conv: AgentConversation, reply: str,
                      expect: str | None, zone: ZoneInfo) -> TurnOutput:
        """An open-conversation reply. A live hold keeps the Confirm button up."""
        res = await self._active_hold(db, conv)
        hold = None
        if res is not None:
            local = res.start_at.astimezone(zone)
            hold = {"hold_id": res.id, "party_size": res.party_size,
                    "local_date": local.date().isoformat(),
                    "local_time": local.strftime("%H:%M"),
                    "start_iso": local.isoformat(),
                    "status": "pending_approval" if res.status == PENDING else "held",
                    "held_until": res.held_until.isoformat() if res.held_until else None}
        if expect is None:
            expect = tools.EXPECT_CONFIRM_HOLD if hold is not None else "text"
        return TurnOutput(reply, expect, hold, False, None)  # type: ignore[arg-type]

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
                _append(messages, "user", payload["content"])
            elif type_ == "assistant_turn":
                messages.append({"role": "assistant", "content": _raw(payload)})
            elif type_ == "tool_result":
                _append(messages, "user", _raw(payload))
            elif type_ == "action":
                _append(messages, "user",
                        f"[The diner pressed {payload['action']}: {payload['result']}]")
                _append(messages, "assistant",
                        [{"type": "text", "text": payload["reply"]}])
        return messages


def _raw(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocks as originally sent. JSONB reorders object keys, so the exact
    JSON text is kept in `raw` and replayed from it, byte for byte."""
    raw = payload.get("raw")
    return json.loads(raw) if isinstance(raw, str) else payload["content"]


def _blocks(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _append(messages: list[dict[str, Any]], role: str,
            content: str | list[dict[str, Any]]) -> None:
    """Append a message, merging into the previous one if it has the same role
    (e.g. a typed turn right after an action note)."""
    if messages and messages[-1]["role"] == role:
        prev = messages[-1]
        messages[-1] = {"role": role,
                        "content": _blocks(prev["content"]) + _blocks(content)}
    else:
        messages.append({"role": role, "content": content})
