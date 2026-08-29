# `backend/rate_limit.py` — Sliding-Window Rate Limiter Middleware

## 1. Purpose & Overview

`backend/rate_limit.py` is a self-contained ASGI middleware that throttles clients **per IP address, per endpoint-cost tier, using a sliding-window counter**. It protects the most expensive endpoints (chat, transcription, scraping) from being abused, while leaving cheap reads and monitoring endpoints unrestricted.

The implementation is deliberately minimal:

- **No external dependencies** beyond Starlette (`BaseHTTPMiddleware`, `Request`, `Response`).
- **Pure ASGI logic** that can be imported and tested in isolation (the module docstring at `backend/rate_limit.py:1` calls this out explicitly).
- **In-process state** — sliding-window buckets are stored in memory in a `defaultdict(deque)`. The supported deployment shape is a **single Uvicorn worker**; horizontal scaling would need a shared store (Redis, etc.).

This is wired into `backend/main.py` via:

```python
app.add_middleware(
    RateLimitMiddleware,
    limits={
        "chat":       settings.api.rate_limit_chat_per_minute,
        "transcribe": settings.api.rate_limit_transcribe_per_minute,
        "scrape":     settings.api.rate_limit_scrape_per_minute,
        "default":    settings.api.rate_limit_default_per_minute,
    },
    trust_forwarded_for=settings.api.rate_limit_trust_proxy,
)
```

---

## 2. Architecture

```
HTTP request
     │
     ▼
┌────────────────────────────────────────────────────────────┐
│ RateLimitMiddleware.dispatch                                │
│                                                            │
│ 1. tier = classify_endpoint(method, path)                  │
│      ├─ "OPTIONS" or "/health" or "/docs" → tier = None   │
│      └─ else match (method, path-suffix) against _TIER_RULES│
│                                                            │
│ 2. if tier is None or limit <= 0: pass through            │
│                                                            │
│ 3. result = SlidingWindowLimiter.check(                    │
│        key = (client_ip, tier),                            │
│        limit, window_seconds=60)                           │
│                                                            │
│ 4. on result.allowed → call_next + add X-RateLimit-* headers│
│ 5. on result.allowed == False → 429 JSON + Retry-After     │
└────────────────────────────────────────────────────────────┘
                       │
                       ▼ (allowed only)
                route handler in main.py
```

The keying scheme — `(client_ip, tier)` — means each client gets an **independent** sliding window per tier. Burning your 10/min chat quota doesn't burn your 120/min default read quota.

---

## 3. Key Classes / Functions

### 3.1 Module-level constants

#### `_EXEMPT_PATHS` — `backend/rate_limit.py:18`

```python
_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json", "/favicon.ico"}
```

Endpoints that must never be throttled. Combined with the `OPTIONS` and `GET /health` exemptions inside `classify_endpoint`.

#### `_TIER_RULES` — `backend/rate_limit.py:22`

```python
_TIER_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("POST", "/chat/stream", "chat"),
    ("POST", "/chat",        "chat"),
    ("POST", "/transcribe",  "transcribe"),
    ("POST", "/scrape",      "scrape"),
)
```

Order matters: `"/chat/stream"` is checked before `"/chat"` so a stream request doesn't get matched to the wrong tier. The matching is done via `path.endswith(suffix)` (`backend/rate_limit.py:37`), which transparently handles both `/api/v1/chat` and `/chat`.

### 3.2 `classify_endpoint(method, path)` — `backend/rate_limit.py:30`

```python
def classify_endpoint(method: str, path: str) -> Optional[str]:
    """Map a request to its rate-limit tier name, or None when exempt."""
    if method == "OPTIONS":                          return None
    if method == "GET" and (path.endswith("/health") or path in _EXEMPT_PATHS):
                                                     return None
    for rule_method, suffix, tier in _TIER_RULES:
        if method == rule_method and path.endswith(suffix):
                                                     return tier
    return "default"
```

| Input | Output |
| --- | --- |
| `OPTIONS *` | `None` (always exempt) |
| `GET /api/v1/health` | `None` |
| `GET /docs`, `/redoc`, `/openapi.json`, `/favicon.ico` | `None` |
| `POST /chat` or `POST /chat/stream` (any prefix) | `"chat"` |
| `POST /transcribe` | `"transcribe"` |
| `POST /scrape` | `"scrape"` |
| Anything else | `"default"` |

> The `/health` check uses `path.endswith("/health")`, so both `/health` and `/api/v1/health` are exempt.

### 3.3 `RateLimitResult` — `backend/rate_limit.py:42`

```python
@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: float           # epoch seconds when the window resets (blocked or allowed)
    retry_after: int          # seconds to wait (0 when allowed)
```

`frozen=True` makes it hashable / immutable. The fields are consumed by `dispatch` to populate response headers.

### 3.4 `SlidingWindowLimiter` — `backend/rate_limit.py:51`

```python
class SlidingWindowLimiter:
    """Per-key sliding-window counter backed by timestamp deques."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._buckets: Dict[Tuple[str, str], deque] = defaultdict(deque)
```

#### `check(key, limit, window_seconds) -> RateLimitResult`

```python
def check(self, key, limit, window_seconds):
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
```

| Param | Meaning |
| --- | --- |
| `key` | `(client_ip, tier)` tuple — per-IP, per-tier bucket. |
| `limit` | Max requests allowed within `window_seconds`. |
| `window_seconds` | Sliding window length (always 60 in this app). |

**Algorithm:**

1. Read current time from `self.clock` (injectable for tests — see §9).
2. Drop expired timestamps from the left of the deque (anything older than `now - window_seconds`).
3. If the deque is already at the limit → return `allowed=False`, compute `retry_after` as the time until the **oldest** hit rolls off the window, plus 1 to avoid clock-skew edge cases.
4. Otherwise, append `now` and return `allowed=True` with the new remaining count.

**Why deque?** `append` and `popleft` are both O(1); the deque is naturally ordered by insertion time. Memory per bucket is O(limit).

### 3.5 `RateLimitMiddleware` — `backend/rate_limit.py:74`

```python
class RateLimitMiddleware(BaseHTTPMiddleware):

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
```

| Param | Default | Notes |
| --- | --- | --- |
| `limits` | required | `{tier_name: requests_per_window}`. Tiers not present resolve to `0` and effectively disable that tier. |
| `window_seconds` | `60` | Sliding window length; the app uses 60 (per-minute). |
| `trust_forwarded_for` | `False` | If `True`, read the leftmost IP from `X-Forwarded-For`. **Never** enable this behind an untrusted proxy. |
| `clock` | `time.time` | Injectable for deterministic tests. |

#### `dispatch(request, call_next)` — `backend/rate_limit.py:92`

```python
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
            content={"detail": (
                f"Rate limit exceeded for '{tier}' tier "
                f"({limit} requests per {self._window}s). Retry later."
            )},
            headers=headers,
        )

    response = await call_next(request)
    headers["X-RateLimit-Reset"] = str(int(result.reset_at))
    for name, value in headers.items():
        response.headers[name] = value
    return response
```

Behavior summary:

1. Classify the request into a tier. If exempt (`None`) or the configured limit is zero, short-circuit with `call_next(request)`.
2. Run the sliding-window check.
3. If blocked → respond `429 Too Many Requests` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset`.
4. If allowed → call the downstream handler, then **attach** the three `X-RateLimit-*` headers to whatever response it produced.

> The `X-RateLimit-Reset` header differs slightly between the two branches: when blocked it points at "now + retry_after" (when the next slot becomes available); when allowed it points at `result.reset_at` (when the oldest hit rolls off). This is consistent with the rate-limited/allowed semantics noted in the `RateLimitResult.reset_at` docstring.

#### `_client_ip(request)` — `backend/rate_limit.py:124`

```python
def _client_ip(self, request: Request) -> str:
    if self._trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
```

Returns the leftmost X-Forwarded-For IP when proxy-trust is enabled, otherwise the direct socket peer. Falls back to `"unknown"` when no client is attached (e.g., some test harnesses).

---

## 4. Flow / Lifecycle

### 4.1 Per-request flow

```
incoming HTTP request
     │
     ▼
RateLimitMiddleware.dispatch(request, call_next)
     │
     ├── tier = classify_endpoint(method, path)
     │     ├── OPTIONS / health / docs → tier is None → call_next (no headers added)
     │     └── tier in {"chat","transcribe","scrape","default"} → continue
     │
     ├── limit = self._limits.get(tier, 0); if limit == 0 → call_next
     │
     ├── result = limiter.check((client_ip, tier), limit, 60)
     │     │
     │     ├── expire old deque entries
     │     ├── if len(hits) >= limit → return blocked
     │     └── else hits.append(now) → return allowed
     │
     ├── if blocked:
     │     └── return 429 JSONResponse with Retry-After, X-RateLimit-*
     │
     └── if allowed:
           ├── response = await call_next(request)
           └── attach X-RateLimit-Limit, Remaining, Reset headers → return response
```

### 4.2 Sliding-window mechanics

The deque for a given `(ip, tier)` holds the timestamps of accepted requests within the last 60 seconds. On each new request:

```
   t = now()
   drop entries where entry <= t - 60
   if len(remaining) >= limit → 429
   else append t
```

This guarantees that **at most `limit` requests succeed within any 60-second window** — a stricter guarantee than a fixed-window counter (which would allow up to `2 * limit` at the boundary).

### 4.3 Process lifecycle

- **No startup hook.** The limiter is constructed lazily when `RateLimitMiddleware.__init__` runs, which is during `app.add_middleware(...)` in `backend/main.py:80`.
- **No shutdown hook.** The `defaultdict(deque)` is GC'd with the process. There is no on-disk persistence by design.
- **Process restart empties all buckets.** Each worker starts with zero state.

---

## 5. Dependencies

| Import | Why |
| --- | --- |
| `time` | Default `time.time` clock injected into `SlidingWindowLimiter`. |
| `collections.defaultdict, deque` | The sliding-window bucket store. |
| `dataclasses.dataclass` | `RateLimitResult` (frozen). |
| `typing.{Callable, Dict, Optional, Tuple}` | Type hints. |
| `starlette.middleware.base.{BaseHTTPMiddleware, RequestResponseEndpoint}` | Subclass for the middleware; type hint for `call_next`. |
| `starlette.requests.Request` | Request abstraction. |
| `starlette.responses.{JSONResponse, Response}` | 429 response and the return type. |

That's it. No FastAPI, no Pydantic, no third-party packages — the docstring (`backend/rate_limit.py:1`) advertises this explicitly.

---

## 6. Configuration / Environment variables

The middleware itself accepts no env vars; it is fully driven by constructor arguments supplied in `backend/main.py`. Those values come from `config.py`:

| Setting | Default | Effect |
| --- | --- | --- |
| `settings.api.rate_limit_enabled` | `True` | If `False`, `app.add_middleware(...)` is skipped entirely. |
| `settings.api.rate_limit_trust_proxy` | `False` | Maps to `trust_forwarded_for` argument. |
| `settings.api.rate_limit_chat_per_minute` | `10` | `limits["chat"]` |
| `settings.api.rate_limit_transcribe_per_minute` | `15` | `limits["transcribe"]` |
| `settings.api.rate_limit_scrape_per_minute` | `5` | `limits["scrape"]` |
| `settings.api.rate_limit_default_per_minute` | `120` | `limits["default"]` |

The window is hard-coded to 60 seconds; the only knob is requests-per-minute per tier.

---

## 7. API Endpoints

This module defines **no routes** — it sits between ASGI and the FastAPI router, so it implicitly touches every endpoint:

| Endpoint pattern | Tier | Limit (default) | Exempt? |
| --- | --- | --- | --- |
| `GET /health`, `GET /api/v1/health` | — | — | yes (`classify_endpoint` short-circuits on `/health`) |
| `GET /docs`, `/redoc`, `/openapi.json`, `/favicon.ico` | — | — | yes (`_EXEMPT_PATHS`) |
| `OPTIONS *` | — | — | yes |
| `POST /chat`, `POST /chat/stream` (and aliases) | `chat` | 10/min | no |
| `POST /transcribe` (and aliases) | `transcribe` | 15/min | no |
| `POST /scrape` | `scrape` | 5/min | no |
| `GET /opportunities`, `GET /opportunities/{id}` | `default` | 120/min | no |
| `GET /sessions`, `POST /sessions`, `GET /sessions/{id}/messages`, `DELETE /sessions/{id}` | `default` | 120/min | no |
| `GET /scrape/status` | `default` | 120/min | no |

429 response shape:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 17
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1735658917

{
  "detail": "Rate limit exceeded for 'chat' tier (10 requests per 60s). Retry later."
}
```

Successful responses on rate-limited routes also carry `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.

---

## 8. Error handling

- **No exceptions are caught inside `dispatch`.** If `call_next(request)` raises, it propagates as a normal middleware exception — Starlette will return a 500 to the client. The limiter does not count failed downstream requests specially.
- **`_client_ip`** is defensive: if `request.client` is `None`, it returns the string `"unknown"` rather than raising. This keeps the limiter usable in synthetic test harnesses.
- **JSON-decoding of `Retry-After` / timestamps is not done in this module** — values are pre-formatted to ints/strings before being placed in headers.

---

## 9. Notable patterns / design decisions

- **Pure-Python, dependency-light.** The module's docstring (`backend/rate_limit.py:1`) advertises this; the import list is short by design.
- **Injectable `clock`.** Both `SlidingWindowLimiter` and `RateLimitMiddleware` accept a `clock` callable. Tests can pass a `lambda: 1000.0` and step the clock manually to make sliding-window behavior deterministic.
- **Suffix-based path matching.** Using `path.endswith(suffix)` rather than an exact prefix means the same tier matches `/chat`, `/api/v1/chat`, and any future alias — and the rule tuple covers both `/chat` and `/chat/stream`.
- **Per-tier buckets per IP.** Keying on `(client_ip, tier)` means quota for one tier doesn't bleed into another. Burning 10 chat requests leaves all 120 default read requests intact.
- **Sliding window > fixed window.** Strictly enforces the limit at every point in time, removing the "double-burst at the boundary" weakness of fixed-window counters.
- **`X-RateLimit-Reset` semantics vary by branch.** When blocked, it points at the time the next slot will be free (`now + retry_after`). When allowed, it points at `result.reset_at` (when the oldest in-window hit will roll off). The header name is the same in both cases, which matches common practice.
- **`limit <= 0` short-circuits to bypass.** Lets operators disable a tier (or all tiers) by setting its limit to 0 without removing the middleware.
- **`_EXEMPT_PATHS` is a frozenset-style set lookup.** O(1) membership check, even if the list grows.
- **In-process state — single-worker assumption.** Buckets live in `defaultdict(deque)` on the middleware instance. Multiple Uvicorn workers each have their own state, so a client could effectively get `workers × limit` requests. Document this clearly before scaling out.
- **Trust-proxy is opt-in.** `X-Forwarded-For` is only honored when explicitly enabled. Misconfigured proxy-trust is a common IP-spoofing vector, so the safe default is `False`.
- **Headers added to success responses, not 429-only.** Clients can show "you have 7 requests left" UIs without hitting the rate limit first.