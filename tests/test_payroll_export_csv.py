"""render_csv, on its own. No session, no HTTP.

The formatting rules live in one function precisely so they can be asserted
in one place — a payroll bureau reads this file, and a column that lies is
worse than a column that is missing.
"""

import csv
import io
from datetime import date, datetime, timedelta, timezone

from backend.services.payroll_export import CSV_HEADER, PayrollCsvRow, render_csv

NY = timezone(timedelta(hours=-4))


def _row(**overrides) -> PayrollCsvRow:
    fields = dict(
        employee_id="m1m2m3m4",
        employee_name="Dana Okafor",
        pay_date=date(2026, 9, 15),
        location_id="ab12cd34",
        location_name="Flatbush Ave",
        role_id="r1r2r3r4",
        role_name="Role A",
        start_time=datetime(2026, 9, 15, 22, 0, tzinfo=NY),
        end_time=datetime(2026, 9, 16, 6, 0, tzinfo=NY),
        paid_minutes=480,
        source="checked_in",
        checked_in_at=datetime(2026, 9, 15, 21, 56, tzinfo=NY),
        lateness_minutes=-4,
        timezone="America/New_York",
    )
    fields.update(overrides)
    return PayrollCsvRow(**fields)


def _parse(content: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.lstrip("﻿"))))


def test_the_header_matches_exactly_and_in_order():
    assert _parse(render_csv([]))[0] == CSV_HEADER


def test_the_output_starts_with_a_bom():
    """We ship in 19 locales including Arabic, Bengali, Tamil and Telugu.
    Excel on Windows renders a BOM-less UTF-8 CSV as mojibake, which a payroll
    clerk reads as our bug."""
    assert render_csv([_row()]).startswith("﻿")


def test_the_line_terminator_is_crlf():
    """RFC 4180, which is what Excel and every payroll import template
    expect."""
    content = render_csv([_row()])
    assert content.endswith("\r\n")
    assert "\r\n" in content.rstrip("\r\n")


def test_paid_hours_renders_to_two_decimal_places():
    assert _parse(render_csv([_row()]))[1][CSV_HEADER.index("paid_hours")] == "8.00"
    assert _parse(render_csv([_row(paid_minutes=450)]))[1][
        CSV_HEADER.index("paid_hours")] == "7.50"


def test_timestamps_keep_the_offset_they_were_stored_with():
    row = _parse(render_csv([_row()]))[1]
    assert row[CSV_HEADER.index("start_time")] == "2026-09-15T22:00:00-04:00"
    assert row[CSV_HEADER.index("end_time")] == "2026-09-16T06:00:00-04:00"
    assert row[CSV_HEADER.index("pay_date")] == "2026-09-15"


def test_a_utc_stored_instant_renders_in_the_location_zone():
    """The stored instant may arrive in any offset; the CSV always shows the
    location's local wall-clock face, per the controller ruling that a pay
    file reads in local time, not whatever offset happened to be persisted."""
    row = _row(
        start_time=datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc),
        checked_in_at=datetime(2026, 9, 16, 1, 56, tzinfo=timezone.utc),
        timezone="America/New_York",
    )
    parsed = _parse(render_csv([row]))[1]
    assert parsed[CSV_HEADER.index("start_time")] == "2026-09-15T22:00:00-04:00"
    assert parsed[CSV_HEADER.index("end_time")] == "2026-09-16T06:00:00-04:00"
    assert parsed[CSV_HEADER.index("checked_in_at")] == "2026-09-15T21:56:00-04:00"


def test_an_attested_row_reports_nothing_rather_than_zero():
    """Empty, not "0": we did not observe an on-time arrival, we observed
    nothing. And not "None", which is a Python repr leaking into a pay file."""
    row = _parse(render_csv([
        _row(source="manager_attested", checked_in_at=None,
             lateness_minutes=None)
    ]))[1]
    assert row[CSV_HEADER.index("checked_in_at")] == ""
    assert row[CSV_HEADER.index("lateness_minutes")] == ""
    assert row[CSV_HEADER.index("source")] == "manager_attested"


def test_a_negative_lateness_renders_signed():
    row = _parse(render_csv([_row(lateness_minutes=-4)]))[1]
    assert row[CSV_HEADER.index("lateness_minutes")] == "-4"


def test_a_name_with_a_comma_round_trips():
    row = _parse(render_csv([_row(employee_name="Okafor, Dana")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "Okafor, Dana"


def test_a_devanagari_name_round_trips():
    row = _parse(render_csv([_row(employee_name="दाना ओकाफोर")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "दाना ओकाफोर"


def test_the_attestation_reason_appears_nowhere():
    """It is an internal audit note about one employee, written by their
    manager, and the CSV leaves the building."""
    assert "attestation_reason" not in CSV_HEADER
    assert "reason" not in render_csv([_row(source="manager_attested",
                                            checked_in_at=None,
                                            lateness_minutes=None)])


# --- CSV formula injection (CWE-1236) ---------------------------------------

def test_a_formula_leading_name_is_neutralized():
    """Excel and Sheets treat a leading =, +, -, @, tab or CR as the start of
    a formula. A row is data an employer downloads and opens without
    suspicion; it must never be able to run code or call out to a URL."""
    row = _parse(render_csv([_row(employee_name='=HYPERLINK("http://x")')]))[1]
    assert row[CSV_HEADER.index("employee_name")] == '\'=HYPERLINK("http://x")'


def test_a_name_starting_with_a_hyphen_is_neutralized():
    row = _parse(render_csv([_row(employee_name="-1+1")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "'-1+1"


def test_a_plain_name_is_unchanged():
    row = _parse(render_csv([_row(employee_name="Dana Okafor")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "Dana Okafor"


def test_location_and_role_names_are_also_neutralized():
    row = _parse(render_csv([
        _row(location_name="=cmd", role_name="+SUM(1,1)")
    ]))[1]
    assert row[CSV_HEADER.index("location_name")] == "'=cmd"
    assert row[CSV_HEADER.index("role_name")] == "'+SUM(1,1)"


def test_ids_dates_numbers_and_source_are_never_neutralized():
    """Neutralization is for user-controlled free text only — an id, a date,
    a number or the fixed `source` enum are never rewritten even if they
    happen to look formula-shaped."""
    row = _parse(render_csv([_row(employee_id="=1+1")]))[1]
    assert row[CSV_HEADER.index("employee_id")] == "=1+1"
