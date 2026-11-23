"""Call capacity card (decision 103): request and response bodies."""

from datetime import date

from pydantic import BaseModel, Field


class CapacityLocationOut(BaseModel):
    location_id: str
    name: str


class BusiestOut(BaseModel):
    peak_concurrent: int
    day: str


class PendingRequestOut(BaseModel):
    expected_peak: int
    created_at: str


class CapacityOut(BaseModel):
    voice_locations: list[CapacityLocationOut]
    allocation: int
    busiest: BusiestOut | None
    pending_request: PendingRequestOut | None
    default_staff_transfer_number: str | None
    max_expected_peak: int


class CapacityRequestIn(BaseModel):
    location_ids: list[str] = Field(min_length=1, max_length=500)
    expected_peak: int = Field(ge=1, le=500)
    needed_by: date | None = None
    note: str = Field(default="", max_length=1000)


class TransferNumberIn(BaseModel):
    default_staff_transfer_number: str | None = Field(
        default=None, pattern=r"^\+[1-9]\d{6,14}$")
