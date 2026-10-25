"""The Deputy importer only ever calls a Deputy origin."""
import pytest
from fastapi import HTTPException

from backend.routers.import_deputy import _deputy_origin


@pytest.mark.parametrize(
    "raw, origin",
    [
        ("https://7b309013021026.na.deputy.com", "https://7b309013021026.na.deputy.com"),
        ("https://yourcompany.na.deputy.com/", "https://yourcompany.na.deputy.com"),
        ("  https://YourCompany.NA.Deputy.com/#/roster  ", "https://yourcompany.na.deputy.com"),
        ("yourcompany.au.deputy.com", "https://yourcompany.au.deputy.com"),
        ("http://yourcompany.uk.deputy.com", "https://yourcompany.uk.deputy.com"),
        ("https://yourcompany.na.deputy.com:443/api?x=1", "https://yourcompany.na.deputy.com"),
    ],
)
def test_accepts_deputy_hosts_and_keeps_only_the_origin(raw: str, origin: str) -> None:
    assert _deputy_origin(raw) == origin


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://169.254.169.254",
        "http://169.254.170.2/v2/credentials",
        "https://localhost",
        "https://example.com",
        "https://deputy.com.evil.example",
        "https://evildeputy.com",
        "https://yourcompany.na.deputy.com.evil.example",
        "https://user:pass@yourcompany.na.deputy.com",
        "https://evil.example#@yourcompany.na.deputy.com",
        "https://yourcompany.na.deputy.com:8443",
        "ftp://yourcompany.na.deputy.com",
        "https://[::1]",
        "https://yourcompany.na.deputy.com:notaport",
    ],
)
def test_rejects_everything_else(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _deputy_origin(raw)
    assert exc.value.status_code == 400
