"""Strict loader for eval scenarios (`scenarios/*.json`).

Every level rejects unknown keys, including the floor and rules documents,
which are checked against the FloorIn / RulesSchema fields before pydantic
validates them (those models ignore extra keys on their own). The diner's
`facts` are the one open mapping: any string facts besides name and phone.
"""

import json
import re
import types
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ValidationError

from backend.reservations.agent.models import CHANNELS, OUTCOMES
from backend.reservations.booking import E164
from backend.reservations.floor import FloorIn
from backend.reservations.models import BOOKED, CANCELLED, PENDING
from backend.reservations.rules import RulesSchema

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

POLICIES = ("confirm_when_offered", "decline_first_offer", "never_confirm")
GOAL_KINDS = ("book", "change", "cancel")
EXISTING_STATUSES = (BOOKED, PENDING, CANCELLED)
BOOKING_STATUSES = (BOOKED, PENDING)
_ID = re.compile(r"^[a-z0-9_]+$")


class ScenarioInvalid(ValueError):
    pass


@dataclass(frozen=True)
class LocationSpec:
    name: str
    timezone: str


@dataclass(frozen=True)
class DinerSpec:
    name: str
    phone: str | None = None


@dataclass(frozen=True)
class ExistingReservation:
    party_size: int
    start_local: datetime  # naive: the location's wall clock
    status: str
    diner: DinerSpec
    confirmation_code: str | None = None


@dataclass(frozen=True)
class DinerPersona:
    persona: str
    goal: str
    facts: dict[str, str]


@dataclass(frozen=True)
class ExpectedBooking:
    party_size: int
    local_date: str  # YYYY-MM-DD in the location's zone
    local_time: str  # HH:MM in the location's zone
    status: str = BOOKED
    confirmation_code: str | None = None


@dataclass(frozen=True)
class Expectation:
    outcomes: tuple[str, ...]  # any of these passes
    booking: ExpectedBooking | None
    handoff: bool | None  # None: either is fine
    cancelled_code: str | None
    max_turns: int
    kept_codes: tuple[str, ...] = ()  # must still be booked/pending, unmoved
    # True: nothing linked to the conversation was ever confirmed. Every
    # linked row has no code and is not booked/seated/completed (a live hold
    # must still be `held`); checker._confirmed lists what counts.
    no_confirmed_reservations: bool = False


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    channel: str
    now: datetime
    location: LocationSpec
    floor: dict[str, Any]
    rules: dict[str, Any]
    existing: tuple[ExistingReservation, ...]
    diner: DinerPersona
    policy: str
    expect: Expectation
    # What the diner is after; the runner's button policy reads it. None:
    # the runner infers it (a confirmation_code fact means change or cancel).
    goal_kind: str | None = None
    source: Path | None = field(default=None, compare=False)

    def floor_in(self) -> FloorIn:
        return FloorIn.model_validate(self.floor)

    def rules_schema(self) -> RulesSchema:
        return RulesSchema.model_validate(self.rules)


# -- strict helpers ------------------------------------------------------


def _obj(value: Any, where: str, required: set[str], optional: set[str] = frozenset()
         ) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioInvalid(f"{where}: expected an object")
    unknown = set(value) - required - set(optional)
    if unknown:
        raise ScenarioInvalid(f"{where}: unknown key(s) {sorted(unknown)}")
    missing = required - set(value)
    if missing:
        raise ScenarioInvalid(f"{where}: missing key(s) {sorted(missing)}")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioInvalid(f"{where}: expected a non-empty string")
    return value


def _int(value: Any, where: str, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ScenarioInvalid(f"{where}: expected an integer >= {minimum}")
    return value


def _one_of(value: Any, where: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ScenarioInvalid(f"{where}: {value!r} is not one of {list(allowed)}")
    return value


def _phone(value: Any, where: str) -> str:
    if not isinstance(value, str) or not E164.match(value):
        raise ScenarioInvalid(f"{where}: phone must be E.164")
    return value


def _model_class(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel inside `annotation` (X, list[X], X | None), if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = typing.get_origin(annotation)
    if origin in (list, tuple, typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            found = _model_class(arg)
            if found is not None:
                return found
    return None


def _forbid_extra(model: type[BaseModel], data: Any, where: str) -> None:
    """Reject keys `model` does not declare, recursively through nested models."""
    if not isinstance(data, dict):
        return  # pydantic reports the type error
    unknown = set(data) - set(model.model_fields)
    if unknown:
        raise ScenarioInvalid(f"{where}: unknown key(s) {sorted(unknown)}")
    for name, info in model.model_fields.items():
        nested = _model_class(info.annotation)
        if nested is None or name not in data:
            continue
        value = data[name]
        items = value if isinstance(value, list) else [value]
        for i, item in enumerate(items):
            _forbid_extra(nested, item, f"{where}.{name}[{i}]" if isinstance(value, list)
                          else f"{where}.{name}")


def _document(model: type[BaseModel], data: Any, where: str) -> dict[str, Any]:
    _forbid_extra(model, data, where)
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise ScenarioInvalid(f"{where}: {exc}") from exc
    return data


# -- sections ------------------------------------------------------------


def _now(value: Any) -> datetime:
    try:
        now = datetime.fromisoformat(_str(value, "now"))
    except ValueError as exc:
        raise ScenarioInvalid("now: not an ISO datetime") from exc
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(None):
        raise ScenarioInvalid("now: must be a UTC instant (e.g. ...T15:00:00Z)")
    return now.astimezone(timezone.utc)


def _location(value: Any) -> LocationSpec:
    v = _obj(value, "location", {"name", "timezone"})
    zone = _str(v["timezone"], "location.timezone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScenarioInvalid(f"location.timezone: unknown zone {zone!r}") from exc
    return LocationSpec(name=_str(v["name"], "location.name"), timezone=zone)


def _existing(value: Any) -> tuple[ExistingReservation, ...]:
    if not isinstance(value, list):
        raise ScenarioInvalid("existing: expected a list")
    out = []
    for i, item in enumerate(value):
        where = f"existing[{i}]"
        v = _obj(item, where, {"party_size", "start_local", "status", "diner"},
                 {"confirmation_code"})
        try:
            start = datetime.fromisoformat(_str(v["start_local"], f"{where}.start_local"))
        except ValueError as exc:
            raise ScenarioInvalid(f"{where}.start_local: not an ISO datetime") from exc
        if start.tzinfo is not None:
            raise ScenarioInvalid(f"{where}.start_local: local wall clock, no offset")
        d = _obj(v["diner"], f"{where}.diner", {"name"}, {"phone"})
        code = v.get("confirmation_code")
        if code is not None:
            code = _str(code, f"{where}.confirmation_code")
        out.append(ExistingReservation(
            party_size=_int(v["party_size"], f"{where}.party_size"),
            start_local=start,
            status=_one_of(v["status"], f"{where}.status", EXISTING_STATUSES),
            diner=DinerSpec(
                name=_str(d["name"], f"{where}.diner.name"),
                phone=_phone(d["phone"], f"{where}.diner.phone") if "phone" in d else None),
            confirmation_code=code))
    codes = [e.confirmation_code for e in out if e.confirmation_code]
    if len(set(codes)) != len(codes):
        raise ScenarioInvalid("existing: duplicate confirmation_code")
    return tuple(out)


def _diner(value: Any) -> DinerPersona:
    v = _obj(value, "diner", {"persona", "goal", "facts"})
    facts = v["facts"]
    if not isinstance(facts, dict) or not all(
            isinstance(k, str) and isinstance(x, str) for k, x in facts.items()):
        raise ScenarioInvalid("diner.facts: expected an object of strings")
    _str(facts.get("name"), "diner.facts.name")
    _phone(facts.get("phone"), "diner.facts.phone")
    return DinerPersona(persona=_str(v["persona"], "diner.persona"),
                        goal=_str(v["goal"], "diner.goal"), facts=dict(facts))


def _booking(value: Any) -> ExpectedBooking | None:
    if value is None:
        return None
    v = _obj(value, "expect.booking", {"party_size", "local_date", "local_time"},
             {"status", "confirmation_code"})
    try:
        d = date.fromisoformat(_str(v["local_date"], "expect.booking.local_date"))
        t = time.fromisoformat(_str(v["local_time"], "expect.booking.local_time"))
    except ValueError as exc:
        raise ScenarioInvalid(f"expect.booking: bad date/time ({exc})") from exc
    code = v.get("confirmation_code")
    return ExpectedBooking(
        party_size=_int(v["party_size"], "expect.booking.party_size"),
        local_date=d.isoformat(), local_time=t.strftime("%H:%M"),
        status=_one_of(v.get("status", BOOKED), "expect.booking.status",
                       BOOKING_STATUSES),
        confirmation_code=_str(code, "expect.booking.confirmation_code")
        if code is not None else None)


def _expect(value: Any) -> Expectation:
    v = _obj(value, "expect",
             {"outcome", "booking", "handoff", "cancelled_code", "max_turns"},
             {"kept_codes", "no_confirmed_reservations"})
    raw = v["outcome"]
    outcomes = tuple(raw) if isinstance(raw, list) else (raw,)
    if not outcomes:
        raise ScenarioInvalid("expect.outcome: empty")
    for o in outcomes:
        _one_of(o, "expect.outcome", OUTCOMES)
    handoff = v["handoff"]
    if handoff is not None and not isinstance(handoff, bool):
        raise ScenarioInvalid("expect.handoff: expected true, false or null")
    cancelled = v["cancelled_code"]
    kept = v.get("kept_codes", [])
    if not isinstance(kept, list):
        raise ScenarioInvalid("expect.kept_codes: expected a list")
    no_confirmed = v.get("no_confirmed_reservations", False)
    if not isinstance(no_confirmed, bool):
        raise ScenarioInvalid("expect.no_confirmed_reservations: expected true or false")
    if no_confirmed and v["booking"] is not None:
        raise ScenarioInvalid("expect: no_confirmed_reservations needs booking null")
    return Expectation(
        no_confirmed_reservations=no_confirmed,
        outcomes=outcomes, booking=_booking(v["booking"]), handoff=handoff,
        cancelled_code=_str(cancelled, "expect.cancelled_code")
        if cancelled is not None else None,
        max_turns=_int(v["max_turns"], "expect.max_turns"),
        kept_codes=tuple(_str(c, "expect.kept_codes") for c in kept))


_TOP = {"id", "title", "channel", "now", "location", "floor", "rules", "existing",
        "diner", "policy", "expect"}


def parse_scenario(data: Any, source: Path | None = None) -> Scenario:
    v = _obj(data, "scenario", _TOP, {"goal_kind"})
    sid = _str(v["id"], "id")
    if not _ID.match(sid):
        raise ScenarioInvalid(f"id: {sid!r} must be lowercase letters, digits, _")
    if source is not None and source.stem != sid:
        raise ScenarioInvalid(f"id: {sid!r} must match the file name {source.name}")
    scenario = Scenario(
        id=sid, title=_str(v["title"], "title"),
        channel=_one_of(v["channel"], "channel", CHANNELS),
        now=_now(v["now"]), location=_location(v["location"]),
        floor=_document(FloorIn, v["floor"], "floor"),
        rules=_document(RulesSchema, v["rules"], "rules"),
        existing=_existing(v["existing"]), diner=_diner(v["diner"]),
        policy=_one_of(v["policy"], "policy", POLICIES),
        expect=_expect(v["expect"]),
        goal_kind=_one_of(v["goal_kind"], "goal_kind", GOAL_KINDS)
        if "goal_kind" in v else None,
        source=source)
    seeded_codes = {e.confirmation_code for e in scenario.existing}
    for code in (scenario.expect.cancelled_code, *scenario.expect.kept_codes):
        if code is not None and code not in seeded_codes:
            raise ScenarioInvalid(f"expect: code {code!r} is not seeded in existing")
    return scenario


def load_scenarios(path: Path = SCENARIO_DIR, ids: list[str] | None = None
                   ) -> list[Scenario]:
    """Load and validate every `*.json` scenario under `path` (sorted by id);
    with `ids`, return those (in that order), failing on an unknown id. Every
    file is validated either way."""
    scenarios: dict[str, Scenario] = {}
    for file in sorted(Path(path).glob("*.json")):
        try:
            data = json.loads(file.read_text())
        except json.JSONDecodeError as exc:
            raise ScenarioInvalid(f"{file.name}: invalid JSON ({exc})") from exc
        try:
            s = parse_scenario(data, file)
        except ScenarioInvalid as exc:
            raise ScenarioInvalid(f"{file.name}: {exc}") from exc
        if s.id in scenarios:
            raise ScenarioInvalid(f"{file.name}: duplicate id {s.id!r}")
        scenarios[s.id] = s
    if ids is None:
        return list(scenarios.values())
    missing = [i for i in ids if i not in scenarios]
    if missing:
        raise ScenarioInvalid(f"unknown scenario id(s): {missing}")
    return [scenarios[i] for i in ids]
