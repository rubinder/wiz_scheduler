"""Validation of the #189 approved (manager) payload schemas."""
import pytest
from pydantic import ValidationError

from backend.schemas.preference_submission import ApprovedPayload, PreferenceSubmissionPayload


def test_weight_required_and_step():
    row = {"day_of_week": 1}
    with pytest.raises(ValidationError):
        ApprovedPayload(day_preferences=[row])
    with pytest.raises(ValidationError):
        ApprovedPayload(day_preferences=[{**row, "weight": 0.55}])
    with pytest.raises(ValidationError):
        ApprovedPayload(day_preferences=[{**row, "weight": 1.1}])
    ApprovedPayload(day_preferences=[{**row, "weight": 0.7}])


@pytest.mark.parametrize("level,ok", [(-1, True), (-0.5, True), (0.5, True), (1, True),
                                      (0, False), (0.7, False), (2, False)])
def test_affinity_level(level, ok):
    row = {"target_employee_id": "a", "relationship": "prefer_with", "level": level}
    if ok:
        ApprovedPayload(affinities=[row])
    else:
        with pytest.raises(ValidationError):
            ApprovedPayload(affinities=[row])


def test_level_missing_and_importance_optional():
    with pytest.raises(ValidationError):
        ApprovedPayload(affinities=[{"target_employee_id": "a", "relationship": "prefer_with"}])
    ApprovedPayload(caps=[{"start_time": "09:00", "end_time": "10:00", "max_per_week": 1,
                           "weight": 0.5}])


def test_cap_of_three_days_and_overnight():
    days = [{"day_of_week": d, "employee_importance": "low"} for d in range(4)]
    with pytest.raises(ValidationError):
        PreferenceSubmissionPayload(day_preferences=days)
    PreferenceSubmissionPayload(day_preferences=days[:3], caps=[
        {"start_time": "22:00", "end_time": "06:00", "max_per_week": 1,
         "employee_importance": "low"}])
