"""Tests for the in-memory sliding-window rate limiting middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.rate_limit import RateLimitMiddleware, classify_endpoint


class FakeClock:
    """Deterministic monotonic clock for window-expiry tests."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


DEFAULT_LIMITS = {"chat": 2, "transcribe": 3, "scrape": 1, "default": 5}


def build_app(clock, limits=None, trust_forwarded=False):
    app = FastAPI()

    @app.post("/api/v1/chat")
    async def chat():
        return {"ok": True}

    @app.post("/api/v1/chat/stream")
    async def chat_stream():
        return {"ok": True}

    @app.post("/api/v1/transcribe")
    async def transcribe():
        return {"ok": True}

    @app.post("/api/v1/scrape")
    async def scrape():
        return {"ok": True}

    @app.get("/api/v1/opportunities")
    async def opportunities():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(
        RateLimitMiddleware,
        limits=limits if limits is not None else DEFAULT_LIMITS,
        trust_forwarded_for=trust_forwarded,
        clock=clock,
    )
    return app


# --- classify_endpoint unit tests --------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/chat", "chat"),
        ("POST", "/api/v1/chat/stream", "chat"),
        ("POST", "/api/chat/stream", "chat"),  # legacy alias
        ("POST", "/api/v1/transcribe", "transcribe"),
        ("POST", "/api/v1/scrape", "scrape"),
        ("GET", "/api/v1/scrape/status", "default"),
        ("GET", "/api/v1/opportunities", "default"),
        ("GET", "/api/v1/health", None),
        ("GET", "/docs", None),
        ("GET", "/openapi.json", None),
        ("OPTIONS", "/api/v1/chat", None),  # CORS preflight
    ],
)
def test_classify_endpoint(method, path, expected):
    assert classify_endpoint(method, path) == expected


# --- middleware behaviour ------------------------------------------------------


def test_headers_on_success_and_block_at_limit():
    client = TestClient(build_app(FakeClock()))
    first = client.post("/api/v1/chat")
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert int(first.headers["X-RateLimit-Reset"]) > 0

    second = client.post("/api/v1/chat")
    assert second.status_code == 200
    assert second.headers["X-RateLimit-Remaining"] == "0"

    third = client.post("/api/v1/chat")
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert "rate limit" in third.json()["detail"].lower()


def test_window_slides_and_allows_again():
    clock = FakeClock()
    client = TestClient(build_app(clock))
    assert client.post("/api/v1/scrape").status_code == 200
    assert client.post("/api/v1/scrape").status_code == 429

    clock.advance(61)
    response = client.post("/api/v1/scrape")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_tiers_are_independent():
    clock = FakeClock()
    client = TestClient(build_app(clock))
    for _ in range(DEFAULT_LIMITS["chat"] + 1):  # exhaust the chat tier
        response = client.post("/api/v1/chat")
    assert response.status_code == 429

    reads = client.get("/api/v1/opportunities")  # default tier untouched
    assert reads.status_code == 200
    assert reads.headers["X-RateLimit-Limit"] == str(DEFAULT_LIMITS["default"])


def test_health_endpoint_is_exempt():
    client = TestClient(build_app(FakeClock()))
    for _ in range(100):
        assert client.get("/health").status_code == 200


def test_forwarded_for_splits_buckets_when_trusted():
    clock = FakeClock()
    client = TestClient(build_app(clock, trust_forwarded=True))
    headers_a = {"X-Forwarded-For": "10.0.0.1"}
    headers_b = {"X-Forwarded-For": "10.0.0.2"}

    for _ in range(DEFAULT_LIMITS["chat"]):
        assert client.post("/api/v1/chat", headers=headers_a).status_code == 200
    assert client.post("/api/v1/chat", headers=headers_a).status_code == 429

    # Different client behind the proxy still has its own full quota.
    assert client.post("/api/v1/chat", headers=headers_b).status_code == 200


def test_forwarded_for_ignored_when_untrusted():
    clock = FakeClock()
    client = TestClient(build_app(clock, trust_forwarded=False))
    spoofed = {"X-Forwarded-For": "10.9.9.9"}

    for _ in range(DEFAULT_LIMITS["chat"]):
        assert client.post("/api/v1/chat", headers=spoofed).status_code == 200

    # Spoofed header must NOT open a fresh bucket; same real client is blocked.
    assert client.post("/api/v1/chat", headers={"X-Forwarded-For": "10.8.8.8"}).status_code == 429


def test_missing_tier_limit_disables_throttling():
    client = TestClient(build_app(FakeClock(), limits={"chat": 2}))
    for _ in range(50):
        assert client.post("/api/v1/transcribe").status_code == 200
