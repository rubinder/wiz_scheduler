"""In-memory sliding-window rate limiter.

Sized for protecting low-volume sensitive endpoints (e.g.
/auth/forgot-password, /auth/login, /auth/register, /schedules/generate)
where a precise burst limit is needed and the AWS WAF minimum
(100 req / 5 min per IP) is too loose. For shared state across uvicorn
workers, replace the in-process dict with a Redis-backed implementation;
we don't pay that complexity yet.

The limiter is keyed on an arbitrary string — typically a source IP, but
the schedule-generate burst cap keys on company_id instead.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict


class SlidingWindowLimiter:
    """Sliding-window counter per arbitrary string key.

    Trade-offs:
      - In-memory: doesn't survive process restart; a malicious actor can
        force a restart but that's already a Denial-of-Service signal worth
        alarming on independently.
      - Per-worker: if uvicorn runs with N workers, the effective limit is
        N × `max_requests`. For 1-2 workers this is acceptable defensive
        padding; tighten via a Redis backend if we ever scale wider.
      - No background cleanup task: deques get pruned lazily on each call,
        and the dict keeps an entry only while at least one request from
        that IP is inside the window.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key: str, now: float | None = None) -> bool:
        """Record a hit for ``key``; return True if within the limit.

        ``now`` is injectable for tests; defaults to ``time.monotonic()``
        which is immune to wall-clock jumps.
        """
        ts = time.monotonic() if now is None else now
        cutoff = ts - self.window_seconds

        with self._lock:
            q = self._hits[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                # At limit. Do NOT record the new hit — we don't want a
                # blocked request to keep extending the deny window
                # forever (turns 5/5min into 5 forever).
                if not q:
                    # Belt-and-braces cleanup; dict entry can drop next call.
                    del self._hits[key]
                return False
            q.append(ts)
            return True

    def reset(self) -> None:
        """For tests only."""
        with self._lock:
            self._hits.clear()


# Backwards-compatible alias. The original class name described its
# original (only) caller; the limiter itself is generic.
IpRateLimiter = SlidingWindowLimiter


def source_ip_from_request(request) -> str:
    """Extract the client source IP from a Starlette/FastAPI Request.

    Trusts the leftmost X-Forwarded-For entry, which AWS ALB sets to the
    original client IP. Falls back to request.client.host when XFF is
    absent (local dev, tests). Returns "unknown" as a last-resort key so
    the limiter never receives an empty string.
    """
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if xff:
        return xff
    if request.client:
        return request.client.host
    return "unknown"


# Module-level singletons. Configured at import time from settings; tests
# reset via .reset() (see tests/conftest.py).
from backend.config import settings  # noqa: E402

forgot_password_limiter = SlidingWindowLimiter(
    max_requests=settings.FORGOT_PASSWORD_RATE_LIMIT_PER_5MIN,
    window_seconds=300,
)

# Per-IP login cap. bcrypt-DoS + credential-stuffing protection.
login_limiter = SlidingWindowLimiter(
    max_requests=settings.LOGIN_RATE_LIMIT_PER_5MIN,
    window_seconds=300,
)

# Per-IP register cap. Cost of a registration is high (Stripe customer +
# DB rows + welcome email) so the threshold is intentionally tight.
register_limiter = SlidingWindowLimiter(
    max_requests=settings.REGISTER_RATE_LIMIT_PER_HOUR,
    window_seconds=3600,
)

# Per-IP cap on /auth/resend-verification. The endpoint mails an address
# the caller supplies, so without this it is a mail-bomb relay.
resend_verification_limiter = SlidingWindowLimiter(
    max_requests=settings.RESEND_VERIFICATION_RATE_LIMIT_PER_HOUR,
    window_seconds=3600,
)

# Per-Company burst cap on AI-mode schedule generation. Keyed on
# company_id, not IP, so a single OG can't sequentially trigger after
# each lock release.
schedule_generate_ai_limiter = SlidingWindowLimiter(
    max_requests=settings.SCHEDULE_GENERATE_AI_BURST_PER_HOUR,
    window_seconds=3600,
)
