"""Unit tests for the sliding-window rate limiter."""
import pytest

from backend.services.rate_limit import IpRateLimiter, SlidingWindowLimiter


def test_ip_rate_limiter_is_sliding_window_alias():
    """IpRateLimiter is kept as a backwards-compat alias for the generic
    SlidingWindowLimiter so existing call sites keep working."""
    assert IpRateLimiter is SlidingWindowLimiter


def test_under_limit_records_and_allows():
    rl = IpRateLimiter(max_requests=3, window_seconds=60)
    assert rl.check_and_record("1.2.3.4", now=0) is True
    assert rl.check_and_record("1.2.3.4", now=5) is True
    assert rl.check_and_record("1.2.3.4", now=10) is True


def test_at_limit_blocks_next_request():
    rl = IpRateLimiter(max_requests=3, window_seconds=60)
    rl.check_and_record("1.2.3.4", now=0)
    rl.check_and_record("1.2.3.4", now=5)
    rl.check_and_record("1.2.3.4", now=10)
    # 4th within the window is blocked.
    assert rl.check_and_record("1.2.3.4", now=15) is False


def test_window_slides_old_hits_drop():
    rl = IpRateLimiter(max_requests=3, window_seconds=60)
    rl.check_and_record("1.2.3.4", now=0)
    rl.check_and_record("1.2.3.4", now=10)
    rl.check_and_record("1.2.3.4", now=20)
    # At t=80, the t=0 hit has aged out (>60s). Should allow.
    assert rl.check_and_record("1.2.3.4", now=80) is True


def test_blocked_request_does_not_extend_deny_window():
    """If we recorded blocked hits, a continuously-pinging attacker would
    extend the deny window forever. The block path skips the append."""
    rl = IpRateLimiter(max_requests=2, window_seconds=60)
    rl.check_and_record("a", now=0)
    rl.check_and_record("a", now=10)
    # Blocked attempts at t=20, 30, 40 should NOT push out the t=10 hit.
    assert rl.check_and_record("a", now=20) is False
    assert rl.check_and_record("a", now=30) is False
    assert rl.check_and_record("a", now=40) is False
    # At t=70, the t=10 hit aged out → one slot freed. Allow.
    assert rl.check_and_record("a", now=70) is True


def test_per_key_isolation():
    rl = IpRateLimiter(max_requests=2, window_seconds=60)
    rl.check_and_record("a", now=0)
    rl.check_and_record("a", now=1)
    # "a" is at limit, "b" is fresh — must allow.
    assert rl.check_and_record("a", now=2) is False
    assert rl.check_and_record("b", now=2) is True


def test_invalid_construction():
    with pytest.raises(ValueError):
        IpRateLimiter(max_requests=0, window_seconds=60)
    with pytest.raises(ValueError):
        IpRateLimiter(max_requests=5, window_seconds=0)


def test_reset_clears_state():
    rl = IpRateLimiter(max_requests=1, window_seconds=60)
    rl.check_and_record("a", now=0)
    assert rl.check_and_record("a", now=1) is False
    rl.reset()
    assert rl.check_and_record("a", now=2) is True
