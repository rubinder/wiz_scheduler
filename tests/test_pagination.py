"""Unit tests for backend.utils.pagination.clamp_limit (#48)."""
from backend.utils.pagination import clamp_limit


def test_none_returns_default():
    assert clamp_limit(None) == 100
    assert clamp_limit(None, default=25) == 25


def test_zero_or_negative_returns_default():
    assert clamp_limit(0) == 100
    assert clamp_limit(-5) == 100


def test_in_band_returns_value():
    assert clamp_limit(10) == 10
    assert clamp_limit(499) == 499


def test_at_max_returns_max():
    assert clamp_limit(500) == 500


def test_over_max_clamps_to_max():
    assert clamp_limit(999) == 500
    assert clamp_limit(10_000_000) == 500


def test_custom_max_respected():
    assert clamp_limit(150, max=100) == 100
    assert clamp_limit(50, max=100) == 50


def test_no_exception_on_absurd_input():
    """clamp_limit must never raise — DB queries should not 500 on
    pathological caller input."""
    # Very large positive int
    assert clamp_limit(2**62) == 500
