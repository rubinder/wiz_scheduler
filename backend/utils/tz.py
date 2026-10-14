"""IANA timezone helpers shared by locations, employees and the scheduler."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_valid_timezone(name: str | None) -> bool:
    """True if *name* is an IANA zone zoneinfo can load (e.g. America/New_York)."""
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
        return False
    return True


def utc_offset_on(tz_name: str, day: str, hm: str = "12:00") -> str:
    """The zone's UTC offset ("-05:00") at local *hm* on *day* (YYYY-MM-DD).

    Computed for the actual date, so a winter shift gets the winter offset.
    Falls back to "+00:00" on any unparseable input -- the scheduling graph
    degrades, it never raises.
    """
    try:
        dt = datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError, OSError):
        return "+00:00"
    offset = dt.strftime("%z")
    return f"{offset[:3]}:{offset[3:]}"
