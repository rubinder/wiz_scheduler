"""render_decision_email (#189): lists approved values, never scale values."""
import re

from backend.services.preference_decision_email import render_decision_email

APPROVED = {
    "max_hours_per_week": 32.0,
    "affinities": [
        {"target_employee_id": "pat00001", "relationship": "prefer_apart", "level": -0.5,
         "entry_date": "2026-10-27", "expiration_date": None},
        {"target_employee_id": "sam00001", "relationship": "never_together", "level": -1.0,
         "entry_date": "2026-10-27", "expiration_date": None},
    ],
    "blackouts": [{"day_of_week": 1, "start_time": "08:00", "end_time": "12:00"}],
    "day_preferences": [
        {"day_of_week": 2, "weight": 0.3, "employee_importance": "high"},
        {"day_of_week": 4, "weight": 0.9, "employee_importance": "low"},
    ],
    "hour_range_preferences": [
        {"start_time": "09:00", "end_time": "17:00", "weight": 0.9,
         "employee_importance": "medium"},
    ],
    "caps": [{"start_time": "22:00", "end_time": "06:00", "max_per_week": 2, "weight": 0.3,
              "employee_importance": None}],
}
NAMES = {"pat00001": "Pat <Q>", "sam00001": "Sam"}


def _render(**kw):
    args = dict(employee_name="Me & Co", decision="approved", approved=APPROVED,
                denial_reason=None, coworker_names=NAMES, group_name="Acme")
    args.update(kw)
    return render_decision_email(**args)


def test_approval_lists_values():
    subject, html = _render()
    assert "approved" in subject.lower()
    for s in ("32", "Tuesday", "08:00", "12:00", "Wednesday", "Friday", "09:00", "17:00",
              "22:00", "06:00", "Pat &lt;Q&gt;", "Sam",
              "Me &amp; Co", "Acme"):
        assert s in html, s
    assert "at most 2 shifts per week between 22:00&ndash;06:00" in html.lower()


def test_approval_has_no_scale_values():
    subject, html = _render()
    text = subject + html
    for bad in ("0.3", "0.9", "-0.5", "-1", "weight", "level", "importance",
                "prefer_apart", "never_together", "prefer_with", "together", "apart"):
        assert bad not in text, bad
    for word in ("low", "medium", "high"):
        assert not re.search(rf"\b{word}\b", text, re.IGNORECASE), word


def test_denial_includes_escaped_reason():
    subject, html = _render(decision="denied", approved=None,
                            denial_reason="Not <now> & later")
    assert "not approved" in subject.lower()
    assert "Not &lt;now&gt; &amp; later" in html
    assert "<now>" not in html


def test_denial_without_reason():
    _, html = _render(decision="denied", approved=None, denial_reason=None)
    assert "Me &amp; Co" in html
