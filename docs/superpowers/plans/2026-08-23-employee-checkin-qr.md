# Employee Check-In with Rotating QR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Employees scan a rotating QR code at their location to check in against their scheduled shift; managers see a live code and a six-month punctuality report.

**Architecture:** One new table (`employee_check_ins`) whose row count *is* the rotation counter. The QR payload is an HMAC digest over `slug|location|local_date|counter`, keyed by a server-side secret, rendered to SVG server-side so the key and derivation never reach a client. Single use is enforced by a unique constraint on `(location_id, local_date, counter)` rather than a lock. Employee identity comes from the JWT, never from the code.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic v2, pytest/pytest-asyncio; React 18 + TypeScript + Vite + Tailwind. New: `segno` (backend QR encoder), `recharts` (frontend chart).

**Spec:** `docs/superpowers/specs/2026-08-23-employee-checkin-qr-design.md`

## Global Constraints

- **Roles are never hardcoded.** No role-name string literal outside `seed.py`.
- **Timezone:** all timestamps carry offsets derived from `location.timezone` via `zoneinfo.ZoneInfo`. Availability-style wall-clock-tagged-UTC does **not** apply here — check-in times are real instants.
- **Multi-tenancy:** every query filters by `company_id` from the JWT.
- **Type hints** on all Python. **Functional components + hooks** in React.
- **New dependencies are limited to exactly `segno` and `recharts`.** Both are explicitly authorised by the user. Adding any third is out of scope.
- **i18n:** `Record<Language, Translations>` in `LanguageContext.tsx` requires every one of the 19 locale files in `frontend/src/i18n/` to carry every key in `en.ts`. Adding a key to `en.ts` alone **breaks the TypeScript build**.
- **Paid-only:** `assert_paid_plan(db, company_id, "check_in")` on every check-in endpoint, including the employee-facing POST.
- **Retention:** `RETENTION_CHECKINS_DAYS = 180`, swept by `run_data_retention`.
- Lint/tests must pass before each commit: `pytest tests/` and `cd frontend && npx tsc --noEmit`.

---

### Task 1: Config, dependency, and the token helper

Pure functions with no database. Everything later depends on these signatures.

**Files:**
- Modify: `backend/config.py` (after `RETENTION_REVOKED_CONSENTS_DAYS`, ~line 175)
- Modify: `backend/requirements.txt`
- Create: `backend/services/check_in_token.py`
- Test: `tests/test_check_in_token.py`

**Interfaces:**
- Consumes: `backend.config.settings`
- Produces:
  - `build_check_in_token(company_slug: str, location_id: str, local_date: date, counter: int) -> str`
  - `verify_check_in_token(token: str, company_slug: str, location_id: str, local_date: date, counter: int) -> bool`
  - `check_in_deep_link(token: str) -> str`
  - `settings.CHECKIN_QR_SECRET: str`, `settings.CHECKIN_MATCH_WINDOW_HOURS: int`, `settings.RETENTION_CHECKINS_DAYS: int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_in_token.py
"""The HMAC payload behind the rotating QR code.

The four inputs are all public — slug, location, date, and a small integer
counter an attacker can enumerate. The secret is what makes the code
unforgeable, so these tests care mostly about what happens when any one input
or the key is wrong.
"""

from datetime import date

import pytest

from backend.config import settings
from backend.services.check_in_token import (
    build_check_in_token,
    check_in_deep_link,
    verify_check_in_token,
)

ARGS = ("acme-corp", "locn0001", date(2026, 8, 23), 0)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


def test_token_verifies_against_its_own_inputs():
    assert verify_check_in_token(build_check_in_token(*ARGS), *ARGS) is True


def test_token_is_stable_for_the_same_inputs():
    assert build_check_in_token(*ARGS) == build_check_in_token(*ARGS)


@pytest.mark.parametrize(
    "wrong",
    [
        ("other-corp", "locn0001", date(2026, 8, 23), 0),
        ("acme-corp", "locn0003", date(2026, 8, 23), 0),
        ("acme-corp", "locn0001", date(2026, 8, 24), 0),
        ("acme-corp", "locn0001", date(2026, 8, 23), 1),
    ],
    ids=["slug", "location", "date", "counter"],
)
def test_token_rejects_when_any_input_differs(wrong):
    assert verify_check_in_token(build_check_in_token(*ARGS), *wrong) is False


def test_counter_advance_invalidates_the_previous_token():
    """This IS the single-use property: recording a check-in raises the
    counter, so the code on screen stops verifying."""
    spent = build_check_in_token("acme-corp", "locn0001", date(2026, 8, 23), 0)
    assert verify_check_in_token(
        spent, "acme-corp", "locn0001", date(2026, 8, 23), 1
    ) is False


def test_token_rejects_under_a_different_key(monkeypatch):
    token = build_check_in_token(*ARGS)
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "a-different-secret")
    assert verify_check_in_token(token, *ARGS) is False


def test_token_is_url_safe():
    """It is carried in a query string inside the QR payload."""
    token = build_check_in_token(*ARGS)
    assert token
    assert all(c.isalnum() or c in "-_" for c in token)


def test_malformed_token_returns_false_rather_than_raising():
    for junk in ("", "!!!!", "x" * 500):
        assert verify_check_in_token(junk, *ARGS) is False


def test_missing_secret_refuses_to_build(monkeypatch):
    """A predictable key is the same as no key — never fall back to a default."""
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "")
    with pytest.raises(RuntimeError, match="CHECKIN_QR_SECRET"):
        build_check_in_token(*ARGS)


def test_deep_link_embeds_the_token_and_location():
    link = check_in_deep_link("abc123", "locn0001")
    assert link.endswith("/employee/check-in?t=abc123&l=locn0001")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_check_in_token.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.check_in_token'`

- [ ] **Step 3: Add config settings**

In `backend/config.py`, immediately after `RETENTION_REVOKED_CONSENTS_DAYS: int = 365`:

```python
    # Check-in retention. Six months, per issue #63.
    RETENTION_CHECKINS_DAYS: int = 180

    # Rotating check-in QR code.
    #
    # The payload is an HMAC over company slug, location, local date and the
    # count of check-ins already recorded that day. Those four inputs are all
    # public or guessable — the counter is a small integer anyone can
    # enumerate — so this key is the only thing stopping an employee at home
    # from computing a valid code. Injected from AWS Secrets Manager in
    # production, the same way DEMO_SEED_PASSWORD is; there is deliberately no
    # usable default, because a predictable key is the same as no key.
    CHECKIN_QR_SECRET: str = ""

    # How far from a shift's start a scan can land and still be matched to it.
    # Matching on the timestamp rather than the calendar date is what lets
    # shifts crossing midnight work without a special case.
    CHECKIN_MATCH_WINDOW_HOURS: int = 6
```

- [ ] **Step 4: Add the dependency**

Append to `backend/requirements.txt`:

```
segno==1.6.1
```

Then install: `cd backend && uv pip install -r requirements.txt`

- [ ] **Step 5: Write the implementation**

```python
# backend/services/check_in_token.py
"""The HMAC payload behind the rotating check-in QR code.

Issue #63 proposed deriving the code from company slug, location, date, and
the number of employees already checked in that day. Every one of those is
known or guessable to someone who is not on site — the counter is a small
integer an attacker can simply try values for — so on their own they rotate
the code without making it unforgeable.

Running them through an HMAC keyed by a server-side secret keeps the rotation
behaviour exactly as specified while making the code impossible to derive
off-site. Nothing here touches the database: the counter is supplied by the
caller, which is what keeps this module a pure, trivially testable unit.
"""

import base64
import hashlib
import hmac
from datetime import date

from backend.config import settings

# 32 base64url chars ~ 192 bits of the digest. Full SHA-256 would make a
# denser QR for no security gain at this size.
_TOKEN_CHARS = 32


def _secret() -> bytes:
    secret = settings.CHECKIN_QR_SECRET
    if not secret:
        raise RuntimeError(
            "CHECKIN_QR_SECRET is unset. Check-in codes cannot be issued "
            "without it — falling back to a default would make every code "
            "derivable off-site, which is the attack the HMAC exists to stop."
        )
    return secret.encode("utf-8")


def _message(
    company_slug: str, location_id: str, local_date: date, counter: int
) -> bytes:
    return f"{company_slug}|{location_id}|{local_date.isoformat()}|{counter}".encode()


def build_check_in_token(
    company_slug: str, location_id: str, local_date: date, counter: int
) -> str:
    """The QR payload for one specific (location, day, counter) position."""
    digest = hmac.new(
        _secret(),
        _message(company_slug, location_id, local_date, counter),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:_TOKEN_CHARS]


def verify_check_in_token(
    token: str,
    company_slug: str,
    location_id: str,
    local_date: date,
    counter: int,
) -> bool:
    """True if *token* is the code for exactly this position.

    Uses compare_digest rather than ==, which returns early on the first
    differing byte and leaks how much of a guess was right.
    """
    if not token:
        return False
    try:
        expected = build_check_in_token(
            company_slug, location_id, local_date, counter
        )
    except RuntimeError:
        raise
    return hmac.compare_digest(token, expected)


def check_in_deep_link(token: str, location_id: str) -> str:
    """The URL encoded into the QR image.

    A link rather than a bare code so an ordinary phone camera can open it —
    no in-app scanner, no camera permission, no QR *reader* dependency.

    Carries the location because the page has to say which location it is
    checking in to; it carries no identity, which comes from the bearer token
    the app already holds.
    """
    return (
        f"{settings.FRONTEND_URL.rstrip('/')}/employee/check-in"
        f"?t={token}&l={location_id}"
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_check_in_token.py -v`
Expected: PASS, 9 tests (the parametrized case counts as 4)

- [ ] **Step 7: Add FRONTEND_URL**

**This setting does not exist yet** — verified against `backend/config.py`,
which has only `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL` and
`STRIPE_BILLING_PORTAL_RETURN_URL`, all three hardcoding
`http://localhost:5173`. Add it beside them (~line 84):

```python
    # Base URL of the SPA, used to build the check-in deep link encoded into
    # the QR image. The three STRIPE_*_URL settings above each embed this
    # same origin; this is the first setting to name it on its own, and they
    # could be folded onto it later.
    FRONTEND_URL: str = "http://localhost:5173"
```

Re-run: `pytest tests/test_check_in_token.py -v` — all tests must pass.

- [ ] **Step 8: Commit**

```bash
git add backend/config.py backend/requirements.txt backend/services/check_in_token.py tests/test_check_in_token.py
git commit -m "feat(check-in): HMAC-keyed rotating QR token

The four inputs issue #63 proposed are all public or enumerable, so on their
own they rotate the code without making it unforgeable. Keying them through
an HMAC keeps the specified rotation and closes off-site recompute."
```

---

### Task 2: The `employee_check_ins` model and migration

**Files:**
- Create: `backend/models/employee_check_in.py`
- Modify: `backend/models/__init__.py`
- Create: `backend/alembic/versions/0030_add_employee_check_ins.py`
- Test: `tests/test_check_in_model.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `EmployeeCheckIn` with columns `id, company_id, location_id, employee_id, shift_id, checked_in_at, local_date, counter, status, minutes_from_start, created_at`; status constants `CHECK_IN_MATCHED = "matched"`, `CHECK_IN_NO_SHIFT = "no_shift"`, `CHECK_IN_WRONG_LOCATION = "wrong_location"`, `CHECK_IN_DUPLICATE = "duplicate"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_in_model.py
"""The employee_check_ins table.

The unique constraint is the interesting part: it is what makes a code
single-use, and it is deliberately enforced by the database rather than by
application logic that a future caller could forget.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, EmployeeCheckIn, Location, Region
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE,
    CHECK_IN_MATCHED,
    CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _tenant(db: AsyncSession) -> dict:
    company_id, region_id, location_id, employee_id = _id(), _id(), _id(), _id()
    db.add(Company(id=company_id, name="C", slug=_id()))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="America/New_York"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    await db.commit()
    return {"company_id": company_id, "location_id": location_id,
            "employee_id": employee_id}


def _row(t: dict, counter: int, **kw) -> EmployeeCheckIn:
    return EmployeeCheckIn(
        id=_id(),
        company_id=t["company_id"],
        location_id=t["location_id"],
        employee_id=t["employee_id"],
        checked_in_at=datetime(2026, 8, 23, 9, 3, tzinfo=timezone.utc),
        local_date=date(2026, 8, 23),
        counter=counter,
        status=kw.pop("status", CHECK_IN_MATCHED),
        **kw,
    )


async def test_a_check_in_persists(db_session: AsyncSession):
    t = await _tenant(db_session)
    db_session.add(_row(t, 0, minutes_from_start=3))
    await db_session.commit()

    row = (await db_session.execute(select(EmployeeCheckIn))).scalar_one()
    assert row.status == CHECK_IN_MATCHED
    assert row.minutes_from_start == 3
    assert row.shift_id is None


async def test_two_scans_at_the_same_counter_collide(db_session: AsyncSession):
    """Single use, enforced by the database.

    Two employees scanning the same displayed code both present counter 0.
    Without this constraint both requests read COUNT(*) == 0, both verify,
    and both record.
    """
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()

    db_session.add(_row(t, 0))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_the_next_counter_is_free(db_session: AsyncSession):
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()
    db_session.add(_row(t, 1))
    await db_session.commit()  # does not raise


async def test_the_same_counter_on_another_day_is_free(db_session: AsyncSession):
    """The counter restarts each local day, so it only has to be unique
    within one."""
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()

    row = _row(t, 0)
    row.local_date = date(2026, 8, 24)
    db_session.add(row)
    await db_session.commit()  # does not raise


async def test_status_constants_are_the_four_documented_values():
    assert {CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
            CHECK_IN_WRONG_LOCATION, CHECK_IN_DUPLICATE} == {
        "matched", "no_shift", "wrong_location", "duplicate"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_check_in_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmployeeCheckIn'`

- [ ] **Step 3: Write the model**

```python
# backend/models/employee_check_in.py
"""An employee's arrival, matched to the shift they were scheduled for."""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# A scan matched to a shift at the scanned location.
CHECK_IN_MATCHED = "matched"
# Accepted, but this employee has no shift near this time anywhere.
CHECK_IN_NO_SHIFT = "no_shift"
# Accepted, but their shift today is at a different location.
CHECK_IN_WRONG_LOCATION = "wrong_location"
# Accepted, but they already checked in today. The first scan keeps the
# punctuality number.
CHECK_IN_DUPLICATE = "duplicate"


class EmployeeCheckIn(Base):
    __tablename__ = "employee_check_ins"
    __table_args__ = (
        # Single use, enforced by the database rather than by application
        # logic. Two employees scanning the same displayed code both present
        # the same counter; the first insert wins and the second collides.
        # Without this, both requests read COUNT(*) == N, both verify, and
        # both record — the check-in equivalent of the race assert_can_add
        # takes a row lock to avoid.
        UniqueConstraint(
            "location_id", "local_date", "counter",
            name="uq_employee_check_ins_location_date_counter",
        ),
        Index("ix_employee_check_ins_location_date", "company_id",
              "location_id", "local_date"),
        Index("ix_employee_check_ins_employee_date", "company_id",
              "employee_id", "local_date"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False, index=True
    )
    # Null for no_shift and wrong_location. Also leaves room for a check-out
    # feature later without a second table.
    shift_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("shifts.id"), nullable=True
    )
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The location-local date. Stored rather than derived because "first scan
    # of the day" and the rotation counter are both wall-clock questions at
    # the location, and a consumer that forgets to convert is wrong only for
    # locations west of UTC late in the day — a bug that survives a test suite
    # running in UTC.
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    counter: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Signed: negative early, positive late. Denormalised because the report
    # reads over six months and the shift behind it can be edited,
    # regenerated, or purged by the retention sweeps in that time — any of
    # which would silently rewrite history if this were recomputed on read.
    minutes_from_start: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
```

- [ ] **Step 4: Export it**

In `backend/models/__init__.py`, add the import after the `special_hours_day` line:

```python
from backend.models.employee_check_in import EmployeeCheckIn
```

and add `"EmployeeCheckIn",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_check_in_model.py -v`
Expected: PASS, 5 tests

- [ ] **Step 6: Write the migration**

```python
# backend/alembic/versions/0030_add_employee_check_ins.py
"""add employee_check_ins

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_check_ins",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("shift_id", sa.String(length=8), nullable=True),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("minutes_from_start", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "local_date", "counter",
                            name="uq_employee_check_ins_location_date_counter"),
    )
    op.create_index("ix_employee_check_ins_company_id",
                    "employee_check_ins", ["company_id"])
    op.create_index("ix_employee_check_ins_location_id",
                    "employee_check_ins", ["location_id"])
    op.create_index("ix_employee_check_ins_employee_id",
                    "employee_check_ins", ["employee_id"])
    op.create_index("ix_employee_check_ins_location_date",
                    "employee_check_ins",
                    ["company_id", "location_id", "local_date"])
    op.create_index("ix_employee_check_ins_employee_date",
                    "employee_check_ins",
                    ["company_id", "employee_id", "local_date"])


def downgrade() -> None:
    op.drop_index("ix_employee_check_ins_employee_date",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_location_date",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_employee_id",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_location_id",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_company_id",
                  table_name="employee_check_ins")
    op.drop_table("employee_check_ins")
```

- [ ] **Step 7: Verify the migration applies against real Postgres**

The test suite runs on SQLite, which will not catch a Postgres-only DDL problem.

```bash
docker-compose up -d postgres
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
cd backend && alembic upgrade head && alembic downgrade 0029 && alembic upgrade head
```

Expected: all three succeed. Confirm the constraint exists:

```bash
docker exec wiz_scheduler-postgres-1 psql -U shiftsync -d shiftsync \
  -c "\d employee_check_ins"
```

- [ ] **Step 8: Commit**

```bash
git add backend/models/employee_check_in.py backend/models/__init__.py backend/alembic/versions/0030_add_employee_check_ins.py tests/test_check_in_model.py
git commit -m "feat(check-in): employee_check_ins table

Unique on (location_id, local_date, counter) — the database, not
application logic, is what makes a code single-use."
```

---

### Task 3: The check-in service — counter, matching, recording

The whole decision table lives here so the router stays thin.

**Files:**
- Create: `backend/services/check_in.py`
- Test: `tests/test_check_in_service.py`

**Interfaces:**
- Consumes: Task 1's `verify_check_in_token`; Task 2's `EmployeeCheckIn` and status constants
- Produces:
  - `local_date_for(location: Location, at: datetime) -> date`
  - `async current_counter(db, location_id: str, local_date: date) -> int`
  - `async issue_token(db, company_slug: str, location: Location) -> tuple[str, int]`
  - `async record_check_in(db, company_id: str, employee_id: str, location: Location, company_slug: str, token: str, now: datetime | None = None) -> EmployeeCheckIn`
  - `class CheckInRejected(Exception)` with `.code: str` — codes `invalid_token` and `code_already_used`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_in_service.py
"""Counter, shift matching, and the four statuses.

The timezone cases are load-bearing. A suite that only exercises UTC
locations proves nothing about local_date, and the same blind spot let a
-05:00 availability bug reach production (see PR #77).
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Shift, ShiftSchedule,
)
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE, CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.services.check_in import (
    CheckInRejected, current_counter, issue_token, local_date_for,
    record_check_in,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest.fixture
async def tenant(db_session: AsyncSession) -> dict:
    company_id, region_id, employee_id = _id(), _id(), _id()
    slug = "acme-corp"
    db_session.add(Company(id=company_id, name="C", slug=slug))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    here = Location(id=_id(), company_id=company_id, region_id=region_id,
                    name="Here", timezone="America/New_York")
    there = Location(id=_id(), company_id=company_id, region_id=region_id,
                     name="There", timezone="America/New_York")
    db_session.add_all([here, there])
    db_session.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                            email=f"{employee_id}@example.com",
                            location_ids=[here.id, there.id]))
    await db_session.commit()
    return {"company_id": company_id, "slug": slug, "employee_id": employee_id,
            "here": here, "there": there}


async def _add_shift(db: AsyncSession, t: dict, location: Location,
                     start: datetime) -> Shift:
    sched_id = _id()
    db.add(ShiftSchedule(id=sched_id, company_id=t["company_id"],
                         location_id=location.id,
                         week_start_date=start.date(), status="draft"))
    await db.flush()
    shift = Shift(id=_id(), company_id=t["company_id"], shift_schedule_id=sched_id,
                  location_id=location.id, employee_id=t["employee_id"],
                  role_id=_id(), role_name="Floor Associate", date=start.date(),
                  start_time=start, end_time=start + timedelta(hours=8))
    db.add(shift)
    await db.commit()
    return shift


async def _scan(db: AsyncSession, t: dict, location: Location,
                now: datetime) -> EmployeeCheckIn:
    # The same clock for both calls. The local date is inside the signed
    # message, so issuing against the real clock and recording against a
    # pinned one would never verify.
    token, _ = await issue_token(db, t["slug"], location, now=now)
    return await record_check_in(db, t["company_id"], t["employee_id"],
                                 location, t["slug"], token, now=now)


# --- local_date -------------------------------------------------------------

def test_local_date_uses_the_locations_timezone():
    """23:30 UTC is still the 22nd in New York."""
    loc = Location(id="l", company_id="c", region_id="r", name="L",
                   timezone="America/New_York")
    at = datetime(2026, 8, 23, 3, 30, tzinfo=timezone.utc)
    assert local_date_for(loc, at) == date(2026, 8, 22)


def test_local_date_differs_from_utc_date_across_the_line():
    loc = Location(id="l", company_id="c", region_id="r", name="L",
                   timezone="Asia/Tokyo")
    at = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert at.date() == date(2026, 8, 23)
    assert local_date_for(loc, at) == date(2026, 8, 24)


# --- counter ----------------------------------------------------------------

async def test_counter_starts_at_zero(db_session: AsyncSession, tenant: dict):
    assert await current_counter(
        db_session, tenant["here"].id, date(2026, 8, 23)) == 0


async def test_counter_advances_with_each_recorded_scan(
    db_session: AsyncSession, tenant: dict
):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    assert await current_counter(
        db_session, tenant["here"].id, local_date_for(tenant["here"], now)) == 1


# --- single use -------------------------------------------------------------

async def test_a_spent_token_is_rejected(db_session: AsyncSession, tenant: dict):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    token, _ = await issue_token(db_session, tenant["slug"], tenant["here"],
                                 now=now)
    await record_check_in(db_session, tenant["company_id"], tenant["employee_id"],
                          tenant["here"], tenant["slug"], token, now=now)

    with pytest.raises(CheckInRejected) as exc:
        await record_check_in(db_session, tenant["company_id"],
                              tenant["employee_id"], tenant["here"],
                              tenant["slug"], token, now=now)
    assert exc.value.code == "code_already_used"


async def test_a_garbage_token_is_rejected(db_session: AsyncSession, tenant: dict):
    with pytest.raises(CheckInRejected) as exc:
        await record_check_in(db_session, tenant["company_id"],
                              tenant["employee_id"], tenant["here"],
                              tenant["slug"], "not-a-real-token",
                              now=datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc))
    assert exc.value.code == "invalid_token"


# --- matching ---------------------------------------------------------------

async def test_a_scan_near_the_shift_start_matches(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    shift = await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(minutes=4))

    assert row.status == CHECK_IN_MATCHED
    assert row.shift_id == shift.id
    assert row.minutes_from_start == 4


async def test_arriving_early_is_negative(db_session: AsyncSession, tenant: dict):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start - timedelta(minutes=7))
    assert row.minutes_from_start == -7


async def test_a_shift_crossing_midnight_matches_without_special_casing(
    db_session: AsyncSession, tenant: dict
):
    """22:00-06:00 scanned at 21:55. Matching on the timestamp means the
    calendar dates either side of midnight never enter the query."""
    start = datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)  # 22:00 ET on 23rd
    shift = await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start - timedelta(minutes=5))
    assert row.status == CHECK_IN_MATCHED
    assert row.shift_id == shift.id
    assert row.minutes_from_start == -5


async def test_a_scan_outside_the_window_is_no_shift(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(hours=settings.CHECKIN_MATCH_WINDOW_HOURS + 1))
    assert row.status == CHECK_IN_NO_SHIFT
    assert row.shift_id is None
    assert row.minutes_from_start is None


async def test_the_nearest_shift_wins(db_session: AsyncSession, tenant: dict):
    early = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    late = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], early)
    near = await _add_shift(db_session, tenant, tenant["here"], late)

    row = await _scan(db_session, tenant, tenant["here"],
                      late + timedelta(minutes=2))
    assert row.shift_id == near.id


async def test_scanning_where_you_are_not_scheduled_is_wrong_location(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["there"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(minutes=2))
    assert row.status == CHECK_IN_WRONG_LOCATION
    assert row.shift_id is None


async def test_no_shift_anywhere_is_no_shift_not_wrong_location(
    db_session: AsyncSession, tenant: dict
):
    row = await _scan(db_session, tenant, tenant["here"],
                      datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc))
    assert row.status == CHECK_IN_NO_SHIFT


# --- duplicates -------------------------------------------------------------

async def test_the_second_scan_of_the_day_is_a_duplicate(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    first = await _scan(db_session, tenant, tenant["here"],
                        start + timedelta(minutes=1))
    second = await _scan(db_session, tenant, tenant["here"],
                         start + timedelta(minutes=90))

    assert first.status == CHECK_IN_MATCHED
    assert second.status == CHECK_IN_DUPLICATE
    # The first scan keeps the punctuality number.
    assert first.minutes_from_start == 1


async def test_a_duplicate_still_advances_the_counter(
    db_session: AsyncSession, tenant: dict
):
    """It is a recorded check-in, and the rule is that the code moves on every
    recorded scan — leaving a demonstrably-scanned code live would be worse."""
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    await _scan(db_session, tenant, tenant["here"], now + timedelta(minutes=30))

    assert await current_counter(
        db_session, tenant["here"].id, local_date_for(tenant["here"], now)) == 2


async def test_every_scan_is_recorded(db_session: AsyncSession, tenant: dict):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    await _scan(db_session, tenant, tenant["here"], now + timedelta(minutes=30))

    rows = (await db_session.execute(select(EmployeeCheckIn))).scalars().all()
    assert len(rows) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_check_in_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.check_in'`

- [ ] **Step 3: Write the implementation**

```python
# backend/services/check_in.py
"""Recording an employee's arrival against their scheduled shift.

The whole decision table lives here so the router stays a thin translation
layer between HTTP and this module.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import EmployeeCheckIn, Location, Shift
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE,
    CHECK_IN_MATCHED,
    CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.services.check_in_token import (
    build_check_in_token,
    verify_check_in_token,
)

logger = logging.getLogger(__name__)


class CheckInRejected(Exception):
    """The scan was not recorded at all.

    Distinct from the four statuses, which all describe scans that WERE
    recorded. Only a token that does not verify lands here.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def local_date_for(location: Location, at: datetime) -> date:
    """The calendar date at *location* when *at* happened.

    "First scan of the day" and the rotation counter are both wall-clock
    questions at the store, not UTC ones. A location in Tokyo rolls over
    nine hours before UTC does.
    """
    return at.astimezone(ZoneInfo(location.timezone)).date()


async def current_counter(
    db: AsyncSession, location_id: str, local_date: date
) -> int:
    """How many check-ins this location has recorded on *local_date*.

    This IS the rotation counter — the exact input issue #63 specified. A
    separate counter row would be a second source of truth that can drift
    from the rows it counts.
    """
    result = await db.execute(
        select(func.count(EmployeeCheckIn.id)).where(
            EmployeeCheckIn.location_id == location_id,
            EmployeeCheckIn.local_date == local_date,
        )
    )
    return result.scalar() or 0


async def issue_token(
    db: AsyncSession,
    company_slug: str,
    location: Location,
    now: datetime | None = None,
) -> tuple[str, int]:
    """The code to display right now, and the counter it stands for.

    *now* is injectable so a test can pin the clock. It has to be: the local
    date is part of the signed message, so a token issued against the real
    clock will not verify inside a test that pins a different date.
    """
    today = local_date_for(location, now or datetime.now(timezone.utc))
    counter = await current_counter(db, location.id, today)
    return build_check_in_token(company_slug, location.id, today, counter), counter


async def _match_shift(
    db: AsyncSession,
    company_id: str,
    employee_id: str,
    location_id: str,
    at: datetime,
) -> tuple[Shift | None, str]:
    """Find the shift this scan belongs to, and say what happened.

    Matching on start_time rather than on the calendar date is what lets a
    22:00-06:00 shift work with no special case: the scan at 21:55 is simply
    five minutes from the start, and the date either side of midnight never
    enters the query.
    """
    window = timedelta(hours=settings.CHECKIN_MATCH_WINDOW_HOURS)

    candidates = (await db.execute(
        select(Shift).where(
            Shift.company_id == company_id,
            Shift.employee_id == employee_id,
            Shift.start_time >= at - window,
            Shift.start_time <= at + window,
        )
    )).scalars().all()

    here = [s for s in candidates if s.location_id == location_id]
    if here:
        nearest = min(here, key=lambda s: abs(s.start_time - at))
        return nearest, CHECK_IN_MATCHED

    if candidates:
        # Scheduled in this window, but somewhere else.
        return None, CHECK_IN_WRONG_LOCATION

    return None, CHECK_IN_NO_SHIFT


async def record_check_in(
    db: AsyncSession,
    company_id: str,
    employee_id: str,
    location: Location,
    company_slug: str,
    token: str,
    now: datetime | None = None,
) -> EmployeeCheckIn:
    """Validate a scanned code and record the arrival.

    Raises CheckInRejected if the code does not verify. Everything else — no
    shift, wrong location, a second scan — is recorded with a status, because
    an employee who turns up unscheduled is something a manager wants to know
    and refusing the scan would throw that away.
    """
    at = now or datetime.now(timezone.utc)
    today = local_date_for(location, at)
    counter = await current_counter(db, location.id, today)

    if not verify_check_in_token(token, company_slug, location.id, today, counter):
        # Indistinguishable from the outside: a forged code and a code that
        # someone else just spent both fail to verify. Report the likelier and
        # more actionable one — "scan the new code" — since a spent code is an
        # ordinary event at a shift change and forgery is not.
        raise CheckInRejected(
            "invalid_token",
            "That code is no longer valid. Scan the code on screen again.",
        )

    shift, status = await _match_shift(
        db, company_id, employee_id, location.id, at
    )

    already = (await db.execute(
        select(EmployeeCheckIn.id).where(
            EmployeeCheckIn.company_id == company_id,
            EmployeeCheckIn.employee_id == employee_id,
            EmployeeCheckIn.local_date == today,
        ).limit(1)
    )).scalar_one_or_none()

    minutes = None
    if shift is not None:
        minutes = round((at - shift.start_time).total_seconds() / 60)

    row = EmployeeCheckIn(
        company_id=company_id,
        location_id=location.id,
        employee_id=employee_id,
        shift_id=shift.id if shift is not None else None,
        checked_in_at=at,
        local_date=today,
        counter=counter,
        # A repeat scan is a duplicate whatever it matched: the report filters
        # to `matched`, so this is what keeps a 17:00 re-scan from overwriting
        # an on-time arrival with a nine-hour delay.
        status=CHECK_IN_DUPLICATE if already else status,
        minutes_from_start=minutes,
    )
    db.add(row)

    try:
        await db.commit()
    except IntegrityError:
        # Someone else took this counter between our read and our write. The
        # unique constraint is the arbiter; this is the losing side of a race
        # two people scanning the same displayed code will hit routinely.
        await db.rollback()
        raise CheckInRejected(
            "code_already_used",
            "Someone just used that code. Scan the new one on screen.",
        )

    await db.refresh(row)
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_check_in_service.py -v`
Expected: PASS, 17 tests

- [ ] **Step 5: Commit**

```bash
git add backend/services/check_in.py tests/test_check_in_service.py
git commit -m "feat(check-in): counter, shift matching, and the four statuses

Matching on start_time rather than calendar date means midnight-crossing
shifts need no special case."
```

---

### Task 4: The API router

**Files:**
- Create: `backend/routers/check_ins.py`
- Create: `backend/schemas/check_in.py`
- Modify: `backend/main.py` (import block ~line 10, registration ~line 105)
- Test: `tests/test_check_in_api.py`

**Interfaces:**
- Consumes: Task 3's `issue_token`, `record_check_in`, `current_counter`, `CheckInRejected`; `assert_paid_plan` from `backend.services.plan`
- Produces: `GET /api/v1/check-ins/qr`, `POST /api/v1/check-ins`, `GET /api/v1/check-ins/report`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_in_api.py
"""The check-in endpoints.

Paid gating is asserted on the EMPLOYEE endpoint as well as the manager one:
what gates the feature is the tenant's plan, not the caller's role.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, Location, Region, Shift, ShiftSchedule, User,
)
from backend.models.ownership_group import OwnershipGroup
from backend.services.check_in import issue_token
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


async def _tenant(db: AsyncSession, *, paid: bool) -> dict:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(
        id=og_id, name="G",
        stripe_subscription_id="sub_x" if paid else None,
    ))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}",
                   ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location = Location(id=_id(), company_id=company_id, region_id=region_id,
                        name="Here", timezone="America/New_York")
    db.add(location)
    manager_id, employee_user_id, employee_id = _id(), _id(), _id()
    db.add(User(id=manager_id, company_id=company_id,
                email=f"{manager_id}@example.com", hashed_password="x",
                full_name="M", user_role="manager"))
    db.add(User(id=employee_user_id, company_id=company_id,
                email=f"{employee_user_id}@example.com", hashed_password="x",
                full_name="E", user_role="employee"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com",
                    location_ids=[location.id], user_id=employee_user_id))
    await db.commit()
    return {
        "company_id": company_id,
        "slug": f"slug-{company_id}",
        "location": location,
        "employee_id": employee_id,
        "manager_headers": {
            "Authorization":
                f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
        "employee_headers": {
            "Authorization":
                f"Bearer {_make_token(employee_user_id, company_id, 'employee')}"},
    }


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> dict:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> dict:
    return await _tenant(db_session, paid=False)


# --- QR endpoint ------------------------------------------------------------

async def test_manager_gets_a_qr_svg(client: AsyncClient, paid: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["manager_headers"],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counter"] == 0
    assert body["svg"].lstrip().startswith("<?xml") or "<svg" in body["svg"]


async def test_the_qr_response_never_carries_the_secret(
    client: AsyncClient, paid: dict
):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["manager_headers"],
    )
    assert "test-secret-value" not in resp.text


async def test_an_employee_cannot_read_the_qr(client: AsyncClient, paid: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["employee_headers"],
    )
    assert resp.status_code == 403


async def test_qr_is_paid_only(client: AsyncClient, free: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={free['location'].id}",
        headers=free["manager_headers"],
    )
    assert resp.status_code == 402


async def test_qr_refuses_another_tenants_location(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    other = await _tenant(db_session, paid=True)
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={other['location'].id}",
        headers=paid["manager_headers"],
    )
    assert resp.status_code == 404


# --- check-in endpoint ------------------------------------------------------

async def test_employee_checks_in(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])

    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": token, "location_id": paid["location"].id},
        headers=paid["employee_headers"],
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "no_shift"


async def test_a_spent_code_returns_409_not_500(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    body = {"token": token, "location_id": paid["location"].id}
    await client.post("/api/v1/check-ins", json=body,
                      headers=paid["employee_headers"])

    resp = await client.post("/api/v1/check-ins", json=body,
                             headers=paid["employee_headers"])

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] in {"invalid_token", "code_already_used"}


async def test_check_in_is_paid_only(
    client: AsyncClient, db_session: AsyncSession, free: dict
):
    token, _ = await issue_token(db_session, free["slug"], free["location"])
    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": token, "location_id": free["location"].id},
        headers=free["employee_headers"],
    )
    assert resp.status_code == 402


async def test_check_in_requires_authentication(client: AsyncClient, paid: dict):
    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": "anything", "location_id": paid["location"].id},
    )
    assert resp.status_code in (401, 403)


# --- report -----------------------------------------------------------------

async def test_report_returns_rows(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    await client.post("/api/v1/check-ins",
                      json={"token": token, "location_id": paid["location"].id},
                      headers=paid["employee_headers"])

    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid["manager_headers"])

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["rows"]) == 1


async def test_report_filters_by_employee(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    await client.post("/api/v1/check-ins",
                      json={"token": token, "location_id": paid["location"].id},
                      headers=paid["employee_headers"])

    resp = await client.get(
        f"/api/v1/check-ins/report?employee_id={_id()}",
        headers=paid["manager_headers"],
    )
    assert resp.json()["rows"] == []


async def test_report_is_manager_only(client: AsyncClient, paid: dict):
    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid["employee_headers"])
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_check_in_api.py -v`
Expected: FAIL — 404 on every route, since the router is not registered

- [ ] **Step 3: Write the schemas**

```python
# backend/schemas/check_in.py
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CheckInQrResponse(BaseModel):
    """What the manager's screen renders. Deliberately no token field — the
    payload is inside the SVG, and there is no reason for it to be readable
    as text in the page."""

    counter: int
    svg: str
    checked_in_today: int


class CheckInRequest(BaseModel):
    token: str
    location_id: str


class CheckInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    checked_in_at: datetime
    local_date: date
    minutes_from_start: int | None
    shift_id: str | None


class CheckInReportRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    employee_name: str
    location_id: str
    checked_in_at: datetime
    local_date: date
    status: str
    minutes_from_start: int | None


class CheckInReportResponse(BaseModel):
    rows: list[CheckInReportRow]
    retention_days: int
```

- [ ] **Step 4: Write the router**

```python
# backend/routers/check_ins.py
"""Employee check-in: the rotating QR, the scan, and the punctuality report.

Paid-only in all three directions, including the employee-facing POST — what
gates the feature is the tenant's plan, not the caller's role.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_current_user, get_db, require_manager
from backend.models import Company, Employee, EmployeeCheckIn, Location, User
from backend.schemas.check_in import (
    CheckInQrResponse,
    CheckInReportResponse,
    CheckInReportRow,
    CheckInRequest,
    CheckInResponse,
)
from backend.services.check_in import (
    CheckInRejected,
    issue_token,
    record_check_in,
)
from backend.services.check_in_token import check_in_deep_link
from backend.services.plan import assert_paid_plan

router = APIRouter(prefix="/check-ins", tags=["check-ins"])


async def _company_slug(db: AsyncSession, company_id: str) -> str:
    """The slug is one of the four inputs to the signed QR payload."""
    return (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one()


async def _load_location(
    db: AsyncSession, company_id: str, location_id: str
) -> Location:
    location = (await db.execute(
        select(Location).where(
            Location.id == location_id, Location.company_id == company_id
        )
    )).scalar_one_or_none()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


@router.get("/qr", response_model=CheckInQrResponse)
async def get_check_in_qr(
    location_id: str = Query(...),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CheckInQrResponse:
    """The code to display right now.

    Rendered server-side so the secret and the derivation never reach a
    client. The manager page polls this and swaps the image when `counter`
    moves.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")
    location = await _load_location(db, company_id, location_id)

    slug = await _company_slug(db, company_id)
    token, counter = await issue_token(db, slug, location)

    buf = BytesIO()
    segno.make(check_in_deep_link(token, location.id), error="m").save(
        buf, kind="svg", scale=8, border=2
    )

    return CheckInQrResponse(
        counter=counter,
        svg=buf.getvalue().decode("utf-8"),
        checked_in_today=counter,
    )


@router.post("", response_model=CheckInResponse,
             status_code=status.HTTP_201_CREATED)
async def create_check_in(
    body: CheckInRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckInResponse:
    """Record a scan. Identity comes from the JWT, never from the code."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")
    location = await _load_location(db, company_id, body.location_id)

    employee = (await db.execute(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No employee record is linked to this account",
        )

    slug = await _company_slug(db, company_id)

    try:
        row = await record_check_in(
            db, company_id, str(employee.id), location, slug, body.token
        )
    except CheckInRejected as rejected:
        # 409, not 400: the request was well-formed and the caller is
        # authorised — the code just belongs to a moment that has passed.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": rejected.code, "message": rejected.message},
        )

    return CheckInResponse.model_validate(row)


@router.get("/report", response_model=CheckInReportResponse)
async def get_check_in_report(
    employee_id: str | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CheckInReportResponse:
    """Punctuality over the retained window."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_CHECKINS_DAYS
    )
    query = (
        select(EmployeeCheckIn, Employee.full_name)
        .join(Employee, Employee.id == EmployeeCheckIn.employee_id)
        .where(
            EmployeeCheckIn.company_id == company_id,
            EmployeeCheckIn.checked_in_at >= cutoff,
        )
        .order_by(EmployeeCheckIn.checked_in_at)
    )
    if employee_id:
        query = query.where(EmployeeCheckIn.employee_id == employee_id)

    rows = [
        CheckInReportRow(
            id=row.id,
            employee_id=row.employee_id,
            employee_name=name,
            location_id=row.location_id,
            checked_in_at=row.checked_in_at,
            local_date=row.local_date,
            status=row.status,
            minutes_from_start=row.minutes_from_start,
        )
        for row, name in (await db.execute(query)).all()
    ]
    return CheckInReportResponse(
        rows=rows, retention_days=settings.RETENTION_CHECKINS_DAYS
    )
```

- [ ] **Step 5: Register the router**

In `backend/main.py`, add `check_ins` to the `from backend.routers import (...)` block, then after the `special_hours` registration (~line 104):

```python
    app.include_router(check_ins.router, prefix=api_prefix)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_check_in_api.py -v`
Expected: PASS, 12 tests

- [ ] **Step 7: Check for unused imports**

Run: `cd backend && python -c "import ast,sys; [print(n) for n in []]"` — or
simply read the import block against the file. Both handlers already share
`_company_slug`, so the only thing to confirm is that every name imported at
the top of `check_ins.py` is actually used. Remove any that are not, and
re-run `pytest tests/test_check_in_api.py -v`.

- [ ] **Step 8: Commit**

```bash
git add backend/routers/check_ins.py backend/schemas/check_in.py backend/main.py tests/test_check_in_api.py
git commit -m "feat(check-in): QR, scan and report endpoints

Paid gating on the employee POST too — the tenant's plan gates the feature,
not the caller's role."
```

---

### Task 5: Retention sweep

**Files:**
- Modify: `backend/services/data_retention.py`
- Test: `tests/test_check_in_retention.py`

**Interfaces:**
- Consumes: Task 2's `EmployeeCheckIn`; `settings.RETENTION_CHECKINS_DAYS`
- Produces: `summary["old_check_ins_deleted"]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_in_retention.py
"""Check-ins are swept like every other retained record.

Issue #63 asks for six months of history; without a sweep the table grows
forever and the "6-month" figure is decoration.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, EmployeeCheckIn, Location, Region
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.services.data_retention import run_data_retention
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _seed(db: AsyncSession, ages_in_days: list[int]) -> str:
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=_id()))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, employee_id = _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="UTC"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    await db.flush()

    now = datetime.now(timezone.utc)
    for i, age in enumerate(ages_in_days):
        at = now - timedelta(days=age)
        db.add(EmployeeCheckIn(
            id=_id(), company_id=company_id, location_id=location_id,
            employee_id=employee_id, checked_in_at=at, local_date=at.date(),
            counter=i, status=CHECK_IN_MATCHED, minutes_from_start=0,
        ))
    await db.commit()
    return company_id


async def test_check_ins_past_the_cutoff_are_deleted(db_session: AsyncSession):
    await _seed(db_session, [settings.RETENTION_CHECKINS_DAYS + 10])

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 1
    assert (await db_session.execute(
        select(func.count()).select_from(EmployeeCheckIn)
    )).scalar_one() == 0


async def test_check_ins_inside_the_window_survive(db_session: AsyncSession):
    await _seed(db_session, [1, 30, settings.RETENTION_CHECKINS_DAYS - 1])

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 0
    assert (await db_session.execute(
        select(func.count()).select_from(EmployeeCheckIn)
    )).scalar_one() == 3


async def test_the_sweep_reports_zero_rather_than_omitting_the_key(
    db_session: AsyncSession
):
    """Callers read the summary by key; a missing key is a KeyError, not a
    zero."""
    summary = await run_data_retention(db_session)
    assert summary["old_check_ins_deleted"] == 0


async def test_retention_window_is_about_six_months():
    assert settings.RETENTION_CHECKINS_DAYS == 180
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_check_in_retention.py -v`
Expected: FAIL — `KeyError: 'old_check_ins_deleted'`

- [ ] **Step 3: Add the sweep**

In `backend/services/data_retention.py`, add `EmployeeCheckIn` to the model imports, then insert before `await db.commit()`:

```python
    # 7. Check-ins older than the retained window. Issue #63 specifies six
    #    months of history; without this the table grows without bound and
    #    the figure is decoration.
    cutoff_check_ins = now - timedelta(days=settings.RETENTION_CHECKINS_DAYS)
    result = await db.execute(
        delete(EmployeeCheckIn).where(
            EmployeeCheckIn.checked_in_at < cutoff_check_ins
        )
    )
    summary["old_check_ins_deleted"] = result.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_check_in_retention.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add backend/services/data_retention.py tests/test_check_in_retention.py
git commit -m "feat(check-in): sweep check-ins past the retention window"
```

---

### Task 6: Frontend API client, types, and i18n

Doing i18n first means the two page tasks compile as they are written.

**Files:**
- Create: `frontend/src/api/checkIns.ts`
- Modify: `frontend/src/types/index.ts`
- Modify: all 19 files in `frontend/src/i18n/`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `getCheckInQr(locationId)`, `submitCheckIn(token, locationId)`, `getCheckInReport(employeeId?)`; types `CheckInQr`, `CheckInResult`, `CheckInReportRow`; i18n namespace `checkIn`

- [ ] **Step 1: Add the dependency**

```bash
cd frontend && npm install recharts
```

- [ ] **Step 2: Add the types**

Append to `frontend/src/types/index.ts`:

```typescript
export interface CheckInQr {
  counter: number;
  svg: string;
  checked_in_today: number;
}

export type CheckInStatus =
  | "matched"
  | "no_shift"
  | "wrong_location"
  | "duplicate";

export interface CheckInResult {
  id: string;
  status: CheckInStatus;
  checked_in_at: string;
  local_date: string;
  /** Signed: negative early, positive late. Null when no shift matched. */
  minutes_from_start: number | null;
  shift_id: string | null;
}

export interface CheckInReportRow {
  id: string;
  employee_id: string;
  employee_name: string;
  location_id: string;
  checked_in_at: string;
  local_date: string;
  status: CheckInStatus;
  minutes_from_start: number | null;
}

export interface CheckInReport {
  rows: CheckInReportRow[];
  retention_days: number;
}
```

- [ ] **Step 3: Write the API client**

```typescript
// frontend/src/api/checkIns.ts
import { apiFetch } from "./client";
import type { CheckInQr, CheckInReport, CheckInResult } from "../types";

export function getCheckInQr(locationId: string): Promise<CheckInQr> {
  return apiFetch(`/check-ins/qr?location_id=${encodeURIComponent(locationId)}`);
}

export function submitCheckIn(
  token: string,
  locationId: string
): Promise<CheckInResult> {
  return apiFetch("/check-ins", {
    method: "POST",
    body: JSON.stringify({ token, location_id: locationId }),
  });
}

export function getCheckInReport(employeeId?: string): Promise<CheckInReport> {
  const q = employeeId
    ? `?employee_id=${encodeURIComponent(employeeId)}`
    : "";
  return apiFetch(`/check-ins/report${q}`);
}
```

`apiFetch<T>` is exported from `frontend/src/api/client.ts` (verified). Match
the call style of an existing client such as `frontend/src/api/locations.ts`
— in particular whether it sets `Content-Type` itself or `apiFetch` does.

- [ ] **Step 4: Add the English strings**

In `frontend/src/i18n/en.ts`, add a `checkIn` namespace beside the other page namespaces:

```typescript
  checkIn: {
    qrTitle: "Check-In Code",
    qrDesc:
      "Employees scan this to check in. The code changes each time someone scans, so a photo of it is useless to anyone who isn't here.",
    selectLocation: "Location",
    checkedInToday: "Checked in today",
    reportTitle: "Check-In Report",
    reportDesc:
      "How far from their scheduled start each employee arrived, over the last {days} days. Below the line is early, above is late.",
    allEmployees: "All employees",
    filterEmployee: "Employee",
    minutesLate: "Minutes from scheduled start",
    date: "Date",
    noData: "No check-ins recorded yet.",
    // Employee-facing
    checkingIn: "Checking you in...",
    successMatched: "Checked in. You're {minutes} minutes {direction}.",
    successOnTime: "Checked in, right on time.",
    directionEarly: "early",
    directionLate: "late",
    successNoShift:
      "Checked in, but you're not on the rota near this time. Your manager will see it.",
    successWrongLocation:
      "Checked in, but your shift today is at a different location.",
    successDuplicate: "You already checked in today. This one is noted too.",
    codeExpired: "That code was already used. Scan the new one on screen.",
    failed: "Check-in failed. Ask your manager.",
    noEmployeeRecord: "This account isn't linked to an employee record.",
  },
```

- [ ] **Step 5: Mirror the namespace into the other 18 locales**

`LanguageContext.tsx` types the map as `Record<Language, Translations>` where `Translations` derives from `en`, so **a key present only in `en.ts` fails the build.** Add a translated `checkIn` block to every one of: `ar, bn, de, es, fr, hi, id, ja, mr, pcm, pt, ru, ta, te, tr, ur, vi, zh`.

Keep the `{days}`, `{minutes}` and `{direction}` placeholders verbatim in every locale — they are substituted by `.replace()` at the call site.

- [ ] **Step 6: Verify the build**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0. A missing key in any locale surfaces here as a type error naming the locale.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/checkIns.ts frontend/src/types/index.ts frontend/src/i18n frontend/package.json frontend/package-lock.json
git commit -m "feat(check-in): frontend client, types and copy in all 19 locales"
```

---

### Task 7: Manager QR page

**Files:**
- Create: `frontend/src/pages/manager/CheckInQr.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: Task 6's `getCheckInQr`, `CheckInQr`, `checkIn` i18n namespace
- Produces: route `/manager/check-in-qr`

- [ ] **Step 1: Write the page**

```tsx
// frontend/src/pages/manager/CheckInQr.tsx
import { useCallback, useEffect, useRef, useState } from "react";

import { getCheckInQr } from "../../api/checkIns";
import { listLocations } from "../../api/locations";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { CheckInQr as CheckInQrData, Location } from "../../types";

/** Poll interval. The code only has to change before the NEXT person scans,
 *  and a stale code is already spent, so it correctly fails rather than
 *  letting someone in twice. */
const POLL_MS = 3000;

export default function CheckInQr() {
  const { t } = useLanguage();
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationId, setLocationId] = useState("");
  const [qr, setQr] = useState<CheckInQrData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    listLocations()
      .then((rows) => {
        setLocations(rows);
        if (rows.length > 0) setLocationId(rows[0].id);
      })
      .catch(() => setError(t.checkIn.failed));
  }, [t]);

  const refresh = useCallback(async () => {
    if (!locationId) return;
    try {
      setQr(await getCheckInQr(locationId));
      setError(null);
    } catch {
      setError(t.checkIn.failed);
    }
  }, [locationId, t]);

  useEffect(() => {
    void refresh();
    timer.current = window.setInterval(refresh, POLL_MS);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [refresh]);

  return (
    <div className="p-6">
      <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
        {t.checkIn.qrTitle}
      </h1>
      <p className={`mb-6 max-w-2xl ${text.muted}`}>{t.checkIn.qrDesc}</p>

      <label className={`block mb-2 text-sm ${text.muted}`}>
        {t.checkIn.selectLocation}
      </label>
      <select
        value={locationId}
        onChange={(e) => setLocationId(e.target.value)}
        className="glass-input mb-6"
      >
        {locations.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
      </select>

      {error && <div className="glass-alert-error mb-4">{error}</div>}

      {qr && (
        <div className="flex flex-col items-center">
          <div
            className="bg-white p-4 rounded-xl"
            style={{ width: 320, height: 320 }}
            /* The SVG is generated by our own backend from a value we
               control, never from user input. */
            dangerouslySetInnerHTML={{ __html: qr.svg }}
          />
          <p className={`mt-4 text-sm ${text.muted}`}>
            {t.checkIn.checkedInToday}: {qr.checked_in_today}
          </p>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add the route**

In `frontend/src/App.tsx`, inside the `/manager` route block beside `shift-templates`:

```tsx
            <Route path="check-in-qr" element={<CheckInQr />} />
```

plus the matching import at the top.

- [ ] **Step 3: Add the sidebar link**

In `frontend/src/components/layout/Sidebar.tsx`, append to `postEmployeeManagerLinks`:

```typescript
  { to: "/manager/check-in-qr",             labelKey: "checkInQr" },
```

Add `checkInQr: "Check-In Code"` to the `nav` namespace in **all 19 locale files** — the same `Record<Language, Translations>` constraint applies.

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/manager/CheckInQr.tsx frontend/src/App.tsx frontend/src/components/layout/Sidebar.tsx frontend/src/i18n
git commit -m "feat(check-in): manager QR page, polling for rotation"
```

---

### Task 8: Manager report page and employee check-in page

**Files:**
- Create: `frontend/src/pages/manager/CheckInReport.tsx`
- Create: `frontend/src/pages/employee/CheckIn.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: Task 6's `getCheckInReport`, `submitCheckIn`, `CheckInReportRow`, `CheckInResult`
- Produces: routes `/manager/check-in-report`, `/employee/check-in`

- [ ] **Step 1: Write the report page**

```tsx
// frontend/src/pages/manager/CheckInReport.tsx
import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getCheckInReport } from "../../api/checkIns";
import { listEmployees } from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { CheckInReportRow, Employee } from "../../types";

export default function CheckInReport() {
  const { t } = useLanguage();
  const [rows, setRows] = useState<CheckInReportRow[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [retentionDays, setRetentionDays] = useState(180);

  useEffect(() => {
    listEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, []);

  useEffect(() => {
    getCheckInReport(employeeId || undefined)
      .then((r) => {
        setRows(r.rows);
        setRetentionDays(r.retention_days);
      })
      .catch(() => setRows([]));
  }, [employeeId]);

  /** Only matched rows carry a punctuality number; the rest have no shift to
   *  be early or late against. `duplicate` is excluded by construction, which
   *  is what stops an afternoon re-scan reading as a late arrival. */
  const points = useMemo(
    () =>
      rows
        .filter((r) => r.status === "matched" && r.minutes_from_start !== null)
        .map((r) => ({
          x: new Date(r.local_date).getTime(),
          y: r.minutes_from_start as number,
          name: r.employee_name,
        })),
    [rows]
  );

  return (
    <div className="p-6">
      <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
        {t.checkIn.reportTitle}
      </h1>
      <p className={`mb-6 max-w-2xl ${text.muted}`}>
        {t.checkIn.reportDesc.replace("{days}", String(retentionDays))}
      </p>

      <label className={`block mb-2 text-sm ${text.muted}`}>
        {t.checkIn.filterEmployee}
      </label>
      <select
        value={employeeId}
        onChange={(e) => setEmployeeId(e.target.value)}
        className="glass-input mb-6"
      >
        <option value="">{t.checkIn.allEmployees}</option>
        {employees.map((e) => (
          <option key={e.id} value={e.id}>
            {e.full_name}
          </option>
        ))}
      </select>

      {points.length === 0 ? (
        <p className={text.muted}>{t.checkIn.noData}</p>
      ) : (
        <div style={{ width: "100%", height: 360 }}>
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) => new Date(v).toLocaleDateString()}
                name={t.checkIn.date}
              />
              <YAxis
                dataKey="y"
                type="number"
                name={t.checkIn.minutesLate}
              />
              {/* Zero is on time: below the line arrived early, above late. */}
              <ReferenceLine y={0} stroke="currentColor" />
              <Tooltip
                labelFormatter={(v) => new Date(Number(v)).toLocaleDateString()}
              />
              <Scatter data={points} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}

      <table className="w-full mt-8 text-sm">
        <thead>
          <tr>
            <th className="text-start py-2">{t.checkIn.date}</th>
            <th className="text-start py-2">{t.checkIn.filterEmployee}</th>
            <th className="text-end py-2">{t.checkIn.minutesLate}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="py-1">{r.local_date}</td>
              <td className="py-1">{r.employee_name}</td>
              <td className="py-1 text-end">
                {r.minutes_from_start ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Write the employee page**

```tsx
// frontend/src/pages/employee/CheckIn.tsx
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { submitCheckIn } from "../../api/checkIns";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { CheckInResult } from "../../types";

/** Where the QR deep link lands. The token is in the query string; identity
 *  comes from the bearer token this app already holds, so the code itself
 *  never has to know who is scanning. */
export default function CheckIn() {
  const { t } = useLanguage();
  const [params] = useSearchParams();
  const [result, setResult] = useState<CheckInResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  /** A scan is not idempotent — StrictMode's double-effect would burn two
   *  codes and show the second as a duplicate. */
  const submitted = useRef(false);

  const token = params.get("t") ?? "";
  const locationId = params.get("l") ?? "";

  useEffect(() => {
    if (submitted.current) return;
    submitted.current = true;

    submitCheckIn(token, locationId)
      .then(setResult)
      .catch((e: unknown) => {
        const code =
          typeof e === "object" && e !== null && "code" in e
            ? String((e as { code: unknown }).code)
            : "";
        setError(
          code === "code_already_used" || code === "invalid_token"
            ? t.checkIn.codeExpired
            : t.checkIn.failed
        );
      })
      .finally(() => setBusy(false));
  }, [token, locationId, t]);

  if (busy) return <p className="p-6">{t.checkIn.checkingIn}</p>;
  if (error) return <div className="glass-alert-error m-6">{error}</div>;
  if (!result) return null;

  const minutes = result.minutes_from_start ?? 0;
  let message: string;
  if (result.status === "duplicate") message = t.checkIn.successDuplicate;
  else if (result.status === "no_shift") message = t.checkIn.successNoShift;
  else if (result.status === "wrong_location")
    message = t.checkIn.successWrongLocation;
  else if (minutes === 0) message = t.checkIn.successOnTime;
  else
    message = t.checkIn.successMatched
      .replace("{minutes}", String(Math.abs(minutes)))
      .replace(
        "{direction}",
        minutes < 0 ? t.checkIn.directionEarly : t.checkIn.directionLate
      );

  return <p className={`p-6 text-lg ${text.body}`}>{message}</p>;
}
```

- [ ] **Step 3: Confirm the deep link already carries the location**

Task 1 defines `check_in_deep_link(token, location_id)` and Task 4 calls it
with `location.id`, so the link the QR encodes is already
`/employee/check-in?t=...&l=...` — the page above reads both params. Nothing
to change here; this step exists to make you check rather than assume.

Run: `pytest tests/test_check_in_token.py tests/test_check_in_api.py -v`
Expected: PASS. If `check_in_deep_link` takes only a token, Task 1 was
implemented against a stale brief — fix it to the two-argument form and
re-run.

- [ ] **Step 4: Add routes and the sidebar link**

In `frontend/src/App.tsx`, add both routes with their imports:

```tsx
            <Route path="check-in-report" element={<CheckInReport />} />
```
inside `/manager`, and inside `/employee`:
```tsx
            <Route path="check-in" element={<CheckIn />} />
```

In `Sidebar.tsx`, append `{ to: "/manager/check-in-report", labelKey: "checkInReport" }` to `postEmployeeManagerLinks` and `{ to: "/employee/check-in", labelKey: "checkIn" }` to `employeeLinks`. Add `checkInReport` and `checkIn` to the `nav` namespace in **all 19 locale files**.

- [ ] **Step 5: Verify the build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: both succeed. Note the bundle will grow — recharts is a sizeable dependency and the build already warns above 500 kB.

- [ ] **Step 6: Commit**

```bash
git add frontend/src backend/services/check_in_token.py backend/routers/check_ins.py tests/test_check_in_token.py
git commit -m "feat(check-in): punctuality report and employee check-in page"
```

---

### Task 9: End-to-end verification against Postgres, and docs

The SQLite suite cannot catch a Postgres-only DDL or timezone problem. This is the task that proves the feature actually runs.

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `terraform/README.md` (if it documents secrets)

- [ ] **Step 1: Run the whole suite**

Run: `pytest tests/ -q` and `cd frontend && npx tsc --noEmit && npm run build`
Expected: all pass.

- [ ] **Step 2: Exercise the API against real Postgres**

```bash
docker-compose up -d postgres
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
export CHECKIN_QR_SECRET="local-dev-secret"
cd backend && alembic upgrade head && uvicorn main:app --reload
```

Then, against a paid tenant, confirm by hand:
1. `GET /api/v1/check-ins/qr?location_id=...` returns an SVG and `counter: 0`.
2. Decode the QR (any phone) — it should open `/employee/check-in?t=...&l=...`.
3. POST that token as an employee → 201, and the QR endpoint now reports `counter: 1`.
4. POST the same token again → 409 with `code_already_used` or `invalid_token`.

- [ ] **Step 3: Verify the timezone behaviour on real Postgres**

With a location in `Pacific/Auckland`, record a check-in at a UTC time that is the *next* day locally, and confirm `local_date` is the local date, not the UTC one. This is the exact class of bug that reached production in the availability seed.

- [ ] **Step 4: Document the new secret**

Add `CHECKIN_QR_SECRET` to the README's configuration section and to `terraform/README.md` wherever `DEMO_SEED_PASSWORD` is described, noting it must be set in Secrets Manager before the feature is enabled in production and that the service refuses to issue codes without it.

- [ ] **Step 5: Update CLAUDE.md**

Add a short subsection under Architecture:

```markdown
### Check-In (`backend/services/check_in.py`)

Employees scan a rotating QR to check in against their scheduled shift. The
QR payload is an HMAC over `slug|location|local_date|counter` keyed by
`CHECKIN_QR_SECRET`; the counter is the count of check-ins that location has
recorded that local day, so recording one rotates the code. Single use is
enforced by a unique constraint on `(location_id, local_date, counter)`, not
by application logic. Paid-only via `assert_paid_plan`, retained for
`RETENTION_CHECKINS_DAYS`.
```

- [ ] **Step 6: Commit and open the PR**

```bash
git add README.md CLAUDE.md terraform/README.md
git commit -m "docs(check-in): document CHECKIN_QR_SECRET and the check-in flow"
git push -u origin <branch>
gh pr create --title "feat: employee check-in with rotating QR (#63)" --body "Closes #63 ..."
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| Threat model / HMAC | 1 |
| Data model, unique constraint, migration | 2 |
| `local_date` stored | 2, 3 |
| `minutes_from_start` denormalised | 2, 3 |
| No counter table | 3 |
| QR generation, `compare_digest` | 1, 4 |
| Shift-change queue message | 3 (`CheckInRejected` codes), 8 (copy) |
| Employee deep-link flow | 1, 8 |
| Shift matching + midnight | 3 |
| Four statuses, first-scan-wins | 2, 3 |
| Manager QR tab, 3s poll | 7 |
| Manager report tab, scatter + zero line | 8 |
| Config settings | 1 |
| Retention sweep | 5 |
| Paid gating both endpoints | 4 |
| Testing (incl. non-UTC) | 3, 9 |
| Dependencies `segno`, `recharts` | 1, 6 |

No spec requirement is unassigned.

**Known deviations from the spec, resolved inside the plan**

- The spec's `check_in_deep_link(token)` takes `(token, location_id)` — the employee page needs the location to POST. Corrected in Task 8 Step 3, with the Task 1 test updated in the same step rather than left stale.
- `CheckInQrResponse` carries `checked_in_today` in addition to `counter`. They are the same number today; the field exists so the page's label does not depend on the counter's meaning never changing.

**Placeholder scan:** no TBD/TODO; every code step carries real code.

**Type consistency:** `local_date_for`, `current_counter`, `issue_token`, `record_check_in`, `CheckInRejected.code` are used with identical signatures in Tasks 3, 4 and 9. The four status constants are used identically in Tasks 2, 3, 5 and 8. `CheckInStatus` in TypeScript matches the four Python constants exactly.
