"""redact.text and redact.payload (#259 step 10): what may reach the lake."""

import pytest

from backend.analytics import redact


@pytest.mark.parametrize("raw", [
    "415 555 0100", "(415) 555-0100", "+14155550100", "415.555.0100",
    "4155550100", "555-0100 ext", "+1 (415) 555 0100", "(+1 415) 555 0100",
    "+1 +1 415 555 0100", "++1 (415)(555) 0100", "4 1 5 5 5 5 0 1 0 0",
    "415  -  555  -  0100", "(((415))) 555 0100",
])
def test_phone_like_runs_are_masked(raw: str) -> None:
    out = redact.text(f"call me at {raw} please")
    assert "[phone]" in out
    assert not any(ch.isdigit() for ch in out.replace("[phone]", ""))


def test_short_digit_runs_survive() -> None:
    assert redact.text("table for 4 at 7 30") == "table for 4 at 7 30"
    assert redact.text("party of 12") == "party of 12"
    assert redact.text("19:00:00-05:00") == "19:00:00-05:00"


def test_emails_are_masked() -> None:
    out = redact.text("mail jane.doe+x@example.co.uk now")
    assert out == "mail [email] now"


@pytest.mark.parametrize("code", ["ABC234", "abc234", "2345XY", "234567"])
def test_codes_are_masked(code: str) -> None:
    assert redact.text(f"my code is {code}.") == "my code is [code]."


def test_lowercase_words_and_off_alphabet_tokens_survive() -> None:
    # Lowercase without a digit, or a character outside the alphabet (0, 1, O, I).
    assert redact.text("please dinner ABC012 ABCD1O") == "please dinner ABC012 ABCD1O"


@pytest.mark.parametrize("raw,want", [
    ("QWERTZ", "[code]"),
    ("my code is qwertz", "my code is [code]"),
    ("Confirmation: QwErTz.", "Confirmation: [code]."),
    ("confirmation code was abcdef", "confirmation code was [code]"),
    ("code=bcdfgh", "code=[code]"),
])
def test_all_letter_codes_are_masked_in_context_or_uppercase(raw: str, want: str) -> None:
    assert redact.text(raw) == want


def test_known_codes_are_replaced_exactly_in_any_case() -> None:
    out = redact.text("it was hjkmnp, or Hjkmnp, not hjkmnpq", known_codes=["HJKMNP"])
    assert out == "it was [code], or [code], not hjkmnpq"
    assert redact.payload({"error": "no hjkmnp"}, known_codes=["HJKMNP"])["error"] == "no [code]"


def test_none_and_empty() -> None:
    assert redact.text(None) is None
    assert redact.text("") == ""


def test_payload_keeps_only_the_allowlist() -> None:
    out = redact.payload({
        "party_size": 4, "start_iso": "2026-11-20T19:00:00-05:00",
        "name": "Jane Doe", "phone": "+14155550100", "email": "j@example.com",
        "confirmation_code": "ABC234", "notes": "allergic", "error": "not_found",
        "outcome": "ok",
    }, tool="hold_table")
    assert out == {"name": "hold_table", "party_size": 4,
                   "start": "2026-11-20T19:00:00-05:00", "code_present": True,
                   "error": "not_found", "outcome": "ok"}


def test_payload_never_takes_name_from_the_dict() -> None:
    # In tool inputs and results `name` is the diner (or the location).
    assert "name" not in redact.payload({"name": "Jane Doe"})
    assert redact.payload({"name": "Jane Doe"}) == {"code_present": False}


def test_payload_maps_search_window_and_drops_nested_values() -> None:
    out = redact.payload({"earliest": "18:00", "latest": "20:00",
                          "current": {"confirmation_code": "ABC234", "name": "Jane"}})
    assert out == {"start": "18:00", "end": "20:00", "code_present": True}


def test_payload_redacts_string_values() -> None:
    out = redact.payload({"error": "bad number 415 555 0100"})
    assert out["error"] == "bad number [phone]"


def test_iso_dates_are_not_phone_numbers() -> None:
    assert redact.text("on 2026-11-20 at 19:00") == "on 2026-11-20 at 19:00"
    assert redact.text("2026-11-20T19:00:00-05:00") == "2026-11-20T19:00:00-05:00"
    assert redact.text("party 2 2026-11-20 19:00") == "party 2 2026-11-20 19:00"
    assert redact.text("on 2026-11-20 415 555 0100") == "on 2026-11-20 [phone]"
    # Not a date (month 55), so all eleven digits are one phone number.
    assert redact.text("+1 4155-55-0100") == "[phone]"
