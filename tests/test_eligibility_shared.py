"""Both scheduling paths share one eligibility builder.

They used to hold two independent copies of the same four filters. The
weight-1.0 hard filter is applied inside this function, so a second private
copy would silently reintroduce a path where a hard preference is ignored —
exactly the failure this feature exists to prevent.

Written in the style of tests/test_scheduling_model.py: it reads the source,
because what is being asserted is structural, not behavioural.
"""

import re
from pathlib import Path

from backend.scheduling.prompts import eligible_for_slot

_SCHED = Path(__file__).resolve().parent.parent / "backend" / "scheduling"


def test_local_scheduler_uses_the_shared_builder():
    source = (_SCHED / "local_scheduler.py").read_text()
    assert "eligible_for_slot(" in source, (
        "local_scheduler must call eligible_for_slot rather than filtering "
        "candidates itself — the weight-1.0 hard filter lives in there"
    )


def test_prompts_uses_the_shared_builder():
    source = (_SCHED / "prompts.py").read_text()
    body = source.split("def eligible_for_slot", 1)[1]
    after = body.split("\ndef ", 1)[1] if "\ndef " in body else ""
    assert "eligible_for_slot(" in after, (
        "prompts.py must call eligible_for_slot when rendering SHIFT "
        "REQUIREMENTS rather than filtering candidates inline"
    )


def test_no_path_reimplements_the_blackout_filter():
    """_blackout_blocks should be called in exactly one place: the shared
    builder. A second call site means a second copy of the filter chain."""
    for name in ("local_scheduler.py", "prompts.py"):
        source = (_SCHED / name).read_text()
        calls = re.findall(r"_blackout_blocks\(", source)
        # prompts.py holds the definition (1 hit) plus the shared builder's
        # single call (1 hit). local_scheduler.py should hold none.
        limit = 2 if name == "prompts.py" else 0
        assert len(calls) <= limit, (
            f"{name} calls _blackout_blocks {len(calls)} times; the filter "
            "chain belongs only in eligible_for_slot"
        )


def test_shared_builder_returns_dicts_with_skill():
    prepared = [
        {
            "id": "e1",
            "_role_names": {"Cook"},
            "_day_windows": {"Monday": [("09:00", "17:00")]},
            "roles": [{"role_name": "Cook", "skill_level": 4}],
            "day_blackouts": [],
        }
    ]
    out = eligible_for_slot(prepared, "Monday", "Cook", "09:00", "17:00")
    assert [e["id"] for e in out] == ["e1"]
    assert out[0]["_skill"] == 4


def test_shared_builder_excludes_wrong_role_and_wrong_day():
    prepared = [
        {
            "id": "e1",
            "_role_names": {"Cook"},
            "_day_windows": {"Monday": [("09:00", "17:00")]},
            "roles": [{"role_name": "Cook", "skill_level": 4}],
            "day_blackouts": [],
        }
    ]
    assert eligible_for_slot(prepared, "Monday", "Server", "09:00", "17:00") == []
    assert eligible_for_slot(prepared, "Tuesday", "Cook", "09:00", "17:00") == []
