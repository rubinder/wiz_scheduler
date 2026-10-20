"""Open shifts (#174): a shift with no one on it yet.

Inside the scheduling graph an unfilled slot is still the legacy "VACANT"
placeholder: every node's checks are written against it. It becomes an open
shift at the graph's boundary (emit_result), and everything downstream --
draft JSON, approval, the API -- reads the open shape.

Drafts saved before #174 still hold "VACANT" entries, so every reader of
draft JSON goes through is_open_entry, which accepts both. A CONFLICT entry
is never open: it is a genuine error, not a vacancy.
"""

from typing import Any, Mapping

OPEN_STATUS = "OPEN"
LEGACY_VACANT = "VACANT"


def is_open_entry(entry: Mapping[str, Any]) -> bool:
    """True for a draft entry that is an open shift, in either shape."""
    status = entry.get("status")
    if status == "CONFLICT":
        return False
    if status in (OPEN_STATUS, LEGACY_VACANT):
        return True
    return entry.get("employee_id") in (None, "", LEGACY_VACANT)


def to_open_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *entry* in the open shape. The slot (role, location, date,
    times) is kept; the person and any preference asterisks are cleared."""
    out = dict(entry)
    out["employee_id"] = None
    out["employee_name"] = None
    out["status"] = OPEN_STATUS
    out["preference_violations"] = []
    return out
