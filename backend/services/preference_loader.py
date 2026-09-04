"""Load a company's scheduling preferences in the shape the evaluator reads.

Shared by the scheduling graph (_load_initial_state) and the two hand-edit
routes that re-annotate shifts (#99), so all three see the same rows shaped
the same way. Weights are cast to float: the column is Numeric and
SQLAlchemy returns Decimal, which breaks arithmetic against the plain
floats the scoring code uses.
"""

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.employee import (
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)


async def load_employee_preferences(
    db: AsyncSession, company_id: str
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    prefs: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def _bucket(eid: str) -> Dict[str, List[Dict[str, Any]]]:
        return prefs.setdefault(
            eid,
            {"day_preferences": [], "hour_range_preferences": [], "hour_range_caps": []},
        )

    for dp in (await db.execute(
        select(EmployeeDayPreference)
        .where(EmployeeDayPreference.company_id == company_id)
        .order_by(EmployeeDayPreference.day_of_week)
    )).scalars().all():
        _bucket(str(dp.employee_id))["day_preferences"].append(
            {"day_of_week": dp.day_of_week, "weight": float(dp.weight)}
        )

    for rp in (await db.execute(
        select(EmployeeHourRangePreference)
        .where(EmployeeHourRangePreference.company_id == company_id)
    )).scalars().all():
        _bucket(str(rp.employee_id))["hour_range_preferences"].append(
            {"start_time": rp.start_time, "end_time": rp.end_time, "weight": float(rp.weight)}
        )

    for rc in (await db.execute(
        select(EmployeeHourRangeCap)
        .where(EmployeeHourRangeCap.company_id == company_id)
    )).scalars().all():
        _bucket(str(rc.employee_id))["hour_range_caps"].append(
            {"start_time": rc.start_time, "end_time": rc.end_time,
             "max_per_week": rc.max_per_week, "weight": float(rc.weight)}
        )

    return prefs
