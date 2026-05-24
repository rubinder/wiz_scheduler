from datetime import date as _date, datetime, time

from pydantic import BaseModel


class CreateSpecialHoursDayRequest(BaseModel):
    location_id: str
    date: _date
    open_time: time
    close_time: time
    label: str | None = None
    draft_template_id: str | None = None


class UpdateSpecialHoursDayRequest(BaseModel):
    date: _date | None = None
    open_time: time | None = None
    close_time: time | None = None
    label: str | None = None


class SpecialHoursDayResponse(BaseModel):
    id: str
    company_id: str
    location_id: str
    date: _date
    open_time: time
    close_time: time
    label: str | None
    shift_template_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
