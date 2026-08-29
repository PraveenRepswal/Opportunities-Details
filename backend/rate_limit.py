"""In-memory sliding-window rate limiting middleware, tiered by endpoint cost.

Pure ASGI-level logic with no heavy dependencies so it can be imported and
tested in isolation (see tests/test_rate_limit.py). State lives in-process;
a single uvicorn worker is the supported deployment shape.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Endpoints that must never be throttled (monitoring, docs, CORS preflights).
_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json", "/favicon.ico"}

# Cost tiers, resolved by (method, path suffix) so both the canonical
# /api/v1/* routes and the root/legacy alias routes land on the same tier.
_TIER_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("POST", "/chat/stream", "chat"),
    ("POST", "/chat", "chat"),
    ("POST", "/transcribe", "transcribe"),
    ("POST", "/scrape", "scrape"),
)


def classify_endpoint(method: str, path: str) -> Optional[str]:
    """Map a request to its rate-limit tier name, or None when exempt."""
    if method == "OPTIONS":
        return None
    if method == "GET" and (path.endswith("/health") or path in _EXEMPT_PATHS):
        return None
    for rule_method, suffix, tier in _TIER_RULES:
        if method == rule_method and path.endswith(suffix):
            return tier
    return "default"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    # Epoch second when a slot frees up (blocked) or the window resets (allowed).
    reset_at: float
    retry_after: int


class SlidingWindowLimiter:
    """Per-key sliding-window counter backed by timestamp deques."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._buckets: Dict[Tuple[str, str], deque] = defaultdict(deque)

    def check(self, key: Tuple[str, str], limit: int, window_seconds: int) -> RateLimitResult:
        now = self.clock()
        cutoff = now - window_seconds
        hits = self._buckets[key]

        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(hits[0] + window_seconds - now) + 1)
            return RateLimitResult(False, 0, hits[0] + window_seconds, retry_after)

        hits.append(now)
        return RateLimitResult(True, limit - len(hits), now + window_seconds, 0)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rejects clients exceeding their per-tier quota with 429 + Retry-After."""

    def __init__(
        self,
        app,
        *,
        limits: Dict[str, int],
        window_seconds: int = 60,
        trust_forwarded_for: bool = False,
        clock: Optional[Callable[[], float]] = None,
    ):
        super().__init__(app)
        self._limits = limits
        self._window = window_seconds
        self._trust_forwarded_for = trust_forwarded_for
        self._limiter = SlidingWindowLimiter(clock or time.time)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tier = classify_endpoint(request.method, request.url.path)
        limit = self._limits.get(tier or "", 0)
        if tier is None or limit <= 0:
            return await call_next(request)

        result = self._limiter.check((self._client_ip(request), tier), limit, self._window)
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }

        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after)
            headers["X-RateLimit-Reset"] = str(int(self._limiter.clock() + result.retry_after))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded for '{tier}' tier "
                        f"({limit} requests per {self._window}s). Retry later."
                    )
                },
                headers=headers,
            )

        response = await call_next(request)
        headers["X-RateLimit-Reset"] = str(int(result.reset_at))
        for name, value in headers.items():
            response.headers[name] = value
        return response

    def _client_ip(self, request: Request) -> str:
        if self._trust_forwarded_for:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
