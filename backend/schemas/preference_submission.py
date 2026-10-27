"""Payload and view schemas for employee preference submissions (#189).

Employee-side models never carry ``weight`` or affinity ``level`` (extra is
forbidden, so sending one is a 422). Manager-side ``Approved*`` models extend
them with the required explicit values.
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.employee import _validate_hhmm, _validate_weight_step

Importance = Literal["low", "medium", "high"]
Relationship = Literal["prefer_with", "prefer_apart", "never_together", "neutral"]

MAX_DAY_PREFERENCES = 3
AFFINITY_LEVELS = (-1.0, -0.5, 0.0, 0.5, 1.0)

_FORBID = ConfigDict(extra="forbid")


def _hhmm(v: str) -> str:
    return _validate_hhmm("start_time/end_time", v)


class _TimeRange(BaseModel):
    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        return _hhmm(v)


class _NonZeroRange(_TimeRange):
    @model_validator(mode="after")
    def _reject_zero_length(self):
        if self.start_time == self.end_time:
            raise ValueError("start_time and end_time must not be equal")
        return self


def _weight(v: float) -> float:
    _validate_weight_step(v)
    return v


# ── Employee-side rows ──
class SubmittedAffinity(BaseModel):
    model_config = _FORBID
    target_employee_id: str
    relationship: Relationship
    entry_date: date | None = None
    expiration_date: date | None = None


class SubmittedBlackout(_TimeRange):
    model_config = _FORBID
    day_of_week: int = Field(ge=0, le=6)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class SubmittedDayPreference(BaseModel):
    model_config = _FORBID
    day_of_week: int = Field(ge=0, le=6)
    employee_importance: Importance


class SubmittedHourRangePreference(_NonZeroRange):
    model_config = _FORBID
    employee_importance: Importance


class SubmittedCap(_NonZeroRange):
    model_config = _FORBID
    max_per_week: int = Field(ge=0, le=168)
    employee_importance: Importance


def _unique(items: list, key, label: str) -> None:
    keys = [key(i) for i in items]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate {label}")


class _PayloadChecks(BaseModel):
    """Shared structural checks; subclasses supply the row types."""

    @model_validator(mode="after")
    def _structure(self):
        if len(self.day_preferences) > MAX_DAY_PREFERENCES:  # type: ignore[attr-defined]
            raise ValueError(f"at most {MAX_DAY_PREFERENCES} day preferences")
        _unique(self.day_preferences, lambda r: r.day_of_week, "day_of_week")  # type: ignore[attr-defined]
        _unique(self.hour_range_preferences, lambda r: (r.start_time, r.end_time), "hour range")  # type: ignore[attr-defined]
        _unique(self.caps, lambda r: (r.start_time, r.end_time), "cap range")  # type: ignore[attr-defined]
        _unique(self.affinities, lambda r: r.target_employee_id, "affinity target")  # type: ignore[attr-defined]
        return self


class PreferenceSubmissionPayload(_PayloadChecks):
    model_config = _FORBID
    max_hours_per_week: float | None = Field(default=None, ge=0, le=168)
    affinities: list[SubmittedAffinity] = []
    blackouts: list[SubmittedBlackout] = []
    day_preferences: list[SubmittedDayPreference] = []
    hour_range_preferences: list[SubmittedHourRangePreference] = []
    caps: list[SubmittedCap] = []


# ── Manager-side (approve body) ──
class ApprovedAffinity(SubmittedAffinity):
    level: float

    @field_validator("level")
    @classmethod
    def _level(cls, v: float) -> float:
        if v not in AFFINITY_LEVELS:
            raise ValueError("level must be one of -1, -0.5, 0, 0.5, 1")
        return v


class ApprovedBlackout(SubmittedBlackout):
    pass


class ApprovedDayPreference(BaseModel):
    model_config = _FORBID
    day_of_week: int = Field(ge=0, le=6)
    weight: float = Field(ge=0.0, le=1.0)
    employee_importance: Importance | None = None

    @field_validator("weight")
    @classmethod
    def _step(cls, v: float) -> float:
        return _weight(v)


class ApprovedHourRangePreference(_NonZeroRange):
    model_config = _FORBID
    weight: float = Field(ge=0.0, le=1.0)
    employee_importance: Importance | None = None

    @field_validator("weight")
    @classmethod
    def _step(cls, v: float) -> float:
        return _weight(v)


class ApprovedCap(_NonZeroRange):
    model_config = _FORBID
    max_per_week: int = Field(ge=0, le=168)
    weight: float = Field(ge=0.0, le=1.0)
    employee_importance: Importance | None = None

    @field_validator("weight")
    @classmethod
    def _step(cls, v: float) -> float:
        return _weight(v)


class ApprovedPayload(_PayloadChecks):
    model_config = _FORBID
    max_hours_per_week: float | None = Field(default=None, ge=0, le=168)
    affinities: list[ApprovedAffinity] = []
    blackouts: list[ApprovedBlackout] = []
    day_preferences: list[ApprovedDayPreference] = []
    hour_range_preferences: list[ApprovedHourRangePreference] = []
    caps: list[ApprovedCap] = []


# ── Employee-facing read models (no weight, level or importance) ──
class MyAffinity(BaseModel):
    target_employee_id: str
    target_name: str
    relationship: Relationship
    entry_date: date | None = None
    expiration_date: date | None = None


class MyBlackout(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str


class MyDayPreference(BaseModel):
    day_of_week: int


class MyHourRangePreference(BaseModel):
    start_time: str
    end_time: str


class MyCap(BaseModel):
    start_time: str
    end_time: str
    max_per_week: int


class MyPreferencesView(BaseModel):
    max_hours_per_week: float | None = None
    affinities: list[MyAffinity] = []
    blackouts: list[MyBlackout] = []
    day_preferences: list[MyDayPreference] = []
    hour_range_preferences: list[MyHourRangePreference] = []
    caps: list[MyCap] = []


class PendingSubmission(BaseModel):
    id: str
    submitted_at: datetime
    payload: PreferenceSubmissionPayload


class ApprovedCoworker(BaseModel):
    target_name: str


class ApprovedSummary(BaseModel):
    """Redacted approved payload: coworker names only, no scale values."""

    max_hours_per_week: float | None = None
    coworkers: list[ApprovedCoworker] = []
    blackouts: list[MyBlackout] = []
    day_preferences: list[MyDayPreference] = []
    hour_range_preferences: list[MyHourRangePreference] = []
    caps: list[MyCap] = []


class LastDecision(BaseModel):
    status: Literal["approved", "denied"]
    reviewed_at: datetime | None = None
    denial_reason: str | None = None
    approved: ApprovedSummary | None = None


class MySubmissionState(BaseModel):
    enabled: bool
    current: MyPreferencesView
    pending: PendingSubmission | None = None
    last_decision: LastDecision | None = None


class Coworker(BaseModel):
    id: str
    full_name: str


# ── Manager review (list, detail, deny) ──
class DenyBody(BaseModel):
    model_config = _FORBID
    reason: str | None = Field(default=None, max_length=500)


class ReviewListItem(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    location_ids: list[str]
    submitted_at: datetime


class ReviewEmployee(BaseModel):
    id: str
    full_name: str
    location_ids: list[str]


class ReviewAffinity(MyAffinity):
    level: float


class ReviewDayPreference(MyDayPreference):
    weight: float


class ReviewHourRangePreference(MyHourRangePreference):
    weight: float


class ReviewCap(MyCap):
    weight: float


class ReviewCurrentView(BaseModel):
    """The employee's live values as the manager sees them: with weights and levels."""

    max_hours_per_week: float | None = None
    affinities: list[ReviewAffinity] = []
    blackouts: list[MyBlackout] = []
    day_preferences: list[ReviewDayPreference] = []
    hour_range_preferences: list[ReviewHourRangePreference] = []
    caps: list[ReviewCap] = []


class SubmissionDetail(BaseModel):
    id: str
    status: Literal["pending", "approved", "denied", "superseded"]
    employee: ReviewEmployee
    submitted_payload: PreferenceSubmissionPayload
    current: ReviewCurrentView
    submitted_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    denial_reason: str | None = None
    approved_payload: ApprovedPayload | None = None
