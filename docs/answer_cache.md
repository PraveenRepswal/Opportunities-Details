# `backend/answer_cache.py` — Semantic Answer Cache

> **File:** `backend/answer_cache.py` (200 lines)
> **Purpose:** SQLite-backed cache of (prompt embedding → answer) pairs. Identifies near-duplicate questions by cosine similarity on L2-normalized embeddings and short-circuits the entire retrieval + generation pipeline on cache hit. Pure-stdlib so it can be tested in isolation.

---

## 1. Purpose & Overview

The semantic answer cache is the system's **performance and cost optimization layer**. For single-turn requests (no chat history), the pipeline would otherwise:

1. Embed the prompt (≈10 ms on CPU).
2. Hybrid BM25 + FAISS retrieval (≈20–100 ms).
3. CrossEncoder reranking (≈50–200 ms).
4. Build prompts, call the LLM, stream tokens (≈1–10 s).

If a user asks roughly the same question twice — *"any fully funded PhD scholarships in Germany?"* today and *"fully funded PhD opportunities in Germany"* tomorrow — the system can answer in **<1 ms** by reusing a previous answer.

The cache is **semantic** (not lexical): two questions that paraphrase the same intent hit the cache even when word overlap is zero. It is also:

- **Config-scoped** — switching from Ollama to llama.cpp invalidates the match (different answers might be different).
- **Epoch-scoped** — re-indexing the corpus (`rag_pipeline.reload_documents()` → `bump_epoch()`) invalidates every entry at once.
- **TTL-bounded** — entries older than `ttl_hours` are evicted lazily on lookup.
- **Capacity-capped** — per-config LRU eviction ensures the table never exceeds `max_entries`.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  RAGPipeline.stream_response                                       │
│                                                                    │
│  config_hash = sha1(provider|think|rerank|ollama_url|llama_url)    │
│  query_vec = embeddings.embed_query(prompt)                        │
│                                                                    │
│  SemanticAnswerCache.lookup(prompt, query_vec, config_hash)        │
│      │                                                            │
│      ▼                                                            │
│  SQLite: SELECT id, query_embedding, answer, metadata_json,        │
│                hit_count FROM semantic_answer_cache                │
│         WHERE config_hash = ? AND epoch = ? AND created_at > ?    │
│      │                                                            │
│      ▼                                                            │
│  for each row: similarity = _dot(query_vec, unpack(row.embedding)) │
│       if similarity > best_similarity: best_* = (row, sim)        │
│      │                                                            │
│      ▼                                                            │
│  best_similarity >= self.threshold?                                │
│      │ YES                                                       │
│      ├─► UPDATE hit_count = hit_count + 1, last_hit_at = now       │
│      └─► return {answer, metadata, similarity, hit_count}          │
│      │ NO                                                        │
│      └─► DELETE expired / stale-epoch rows, enforce LRU cap → None│
└────────────────────────────────────────────────────────────────────┘

Cache miss → full RAG generation → answer_cache.store(prompt, query_vec, config_hash, answer, meta)

Re-index event → answer_cache.bump_epoch() → epoch +1 → all entries implicitly stale.
```

### Module-level layout

| Lines         | Section                                                     |
| ------------- | ----------------------------------------------------------- |
| `1–15`        | Module docstring + stdlib imports (`array`, `json`, `sqlite3`, `time`, `typing`) |
| `17–35`       | `_SCHEMA` — two tables + index definition                   |
| `38–49`       | `_pack_embedding`, `_unpack_embedding`, `_dot` — vector utilities |
| `52–200`      | `class SemanticAnswerCache` — the cache itself             |

### SQLite schema

```sql
CREATE TABLE IF NOT EXISTS semantic_answer_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    query_embedding BLOB NOT NULL,        -- 8 bytes per double, packed via array.array('d')
    config_hash TEXT NOT NULL,
    answer TEXT NOT NULL,
    metadata_json TEXT,
    created_at REAL NOT NULL,             -- POSIX timestamp from time.time()
    last_hit_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    epoch INTEGER NOT NULL DEFAULT 0      -- bumped on corpus re-index
);
CREATE INDEX IF NOT EXISTS idx_sac_lookup
    ON semantic_answer_cache(config_hash, epoch, created_at);

CREATE TABLE IF NOT EXISTS semantic_answer_cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- usage: stores 'epoch' → '3', bumped by INSERT...ON CONFLICT DO UPDATE
```

The composite index `(config_hash, epoch, created_at)` exactly matches the WHERE clause used in `lookup()`, so cache scans are O(log n) instead of O(n).

---

## 3. Key Classes & Functions

### 3.1 `_pack_embedding(vector) -> bytes` — `backend/answer_cache.py:38`

```python
def _pack_embedding(vector: Sequence[float]) -> bytes:
    return array.array("d", vector).tobytes()
```

Converts a Python list of `float` into a tight binary representation using the platform-native `double` (`array.typecode 'd'`). A 384-dim E5-small embedding is 384 × 8 = 3072 bytes on disk, far smaller than JSON (~12 KB) and faster to deserialize.

### 3.2 `_unpack_embedding(blob) -> List[float]` — `backend/answer_cache.py:42`

Inverse of `_pack_embedding`. Uses `array.array("d").frombytes(blob)` then `.tolist()` to rebuild the Python list. Constant-time per entry.

### 3.3 `_dot(a, b) -> float` — `backend/answer_cache.py:48`

```python
def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
```

Plain dot product. **Critical assumption:** embeddings are L2-normalized (set by `encode_kwargs={"normalize_embeddings": True}` in `backend/rag.py:344`). Under that assumption, cosine similarity equals dot product, so we save two vector norms (one sqrt and two reductions) per similarity score. With N=384 dimensions, that's ~770 floating-point ops saved per comparison.

### 3.4 `class SemanticAnswerCache` — `backend/answer_cache.py:52`

The cache. Single instance per database file. The pipeline creates exactly one in `RAGPipeline.__init__` (`backend/rag.py:297`).

#### 3.4.1 Constructor — `backend/answer_cache.py:53`

```python
def __init__(
    self,
    db_path,
    threshold: float = 0.93,
    ttl_hours: float = 24.0,
    max_entries: int = 500,
    clock=time.time,
):
    self.db_path = str(db_path)
    self.threshold = float(threshold)
    self.ttl_seconds = float(ttl_hours) * 3600.0
    self.max_entries = int(max_entries)
    self.clock = clock
    self.hits = 0
    self.misses = 0
    with self._connect() as conn:
        conn.executescript(_SCHEMA)
    self.epoch = self._load_epoch()
```

| Param | Default | Source | Meaning |
| ----- | ------- | ------ | ------- |
| `db_path` | (required) | `backend.database.DB_PATH` (`Path("opportunities_chat.db")`) | Same SQLite file as chat sessions and opportunities. |
| `threshold` | `0.93` | `settings.model.semantic_cache_similarity_threshold` | Minimum cosine similarity to count as a hit. Higher = stricter. |
| `ttl_hours` | `24.0` | `settings.model.semantic_cache_ttl_hours` | Entries older than this are evicted lazily. |
| `max_entries` | `500` | `settings.model.semantic_cache_max_entries` | Per-config LRU cap. |
| `clock` | `time.time` | injected | Test seam (`tests/test_answer_cache.py` uses `FakeClock`). |

Schema is created on first connect (`executescript(_SCHEMA)`), and the in-memory `self.epoch` is loaded from the `semantic_answer_cache_meta` table (default 0 if absent).

#### 3.4.2 `_connect() -> sqlite3.Connection` — `backend/answer_cache.py:72`

```python
def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
```

`check_same_thread=False` so FastAPI worker threads can share the connection. `row_factory = sqlite3.Row` so columns can be accessed by name.

> **Caveat:** Each operation opens a new connection rather than reusing one. SQLite handles this fine for low concurrency but limits true parallelism (WAL mode would help; not currently enabled).

#### 3.4.3 `_load_epoch() -> int` — `backend/answer_cache.py:79`

Reads the `epoch` key from `semantic_answer_cache_meta`. Returns 0 if absent (first run).

#### 3.4.4 `bump_epoch() -> int` — `backend/answer_cache.py:86`

```python
def bump_epoch(self) -> int:
    """Invalidate every cached answer (call after a corpus re-index)."""
    self.epoch += 1
    with self._connect() as conn:
        conn.execute(
            "INSERT INTO semantic_answer_cache_meta (key, value) VALUES ('epoch', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(self.epoch),),
        )
    return self.epoch
```

Called from `RAGPipeline.reload_documents` (`backend/rag.py:392`) right after `fetch_opportunity_documents()` succeeds. Crucially, **no rows are physically deleted** — the epoch bump alone makes every existing entry's `epoch < self.epoch`, and the lookup query `WHERE epoch = ?` filters them out automatically. Physical cleanup happens lazily inside `lookup` (`backend/answer_cache.py:126`).

#### 3.4.5 `lookup(query_text, query_embedding, config_hash) -> Optional[Dict]` — `backend/answer_cache.py:99`

The core hit/miss decision.

```python
def lookup(
    self, query_text: str, query_embedding: Sequence[float],
    config_hash: str,
) -> Optional[Dict[str, Any]]:
    now = self.clock()
    cutoff = now - self.ttl_seconds
    best_id = best_similarity = best_hit_count = None
    best_answer = best_metadata = None

    with self._connect() as conn:
        rows = conn.execute(
            "SELECT id, query_embedding, answer, metadata_json, hit_count "
            "FROM semantic_answer_cache "
            "WHERE config_hash = ? AND epoch = ? AND created_at > ?",
            (config_hash, self.epoch, cutoff),
        ).fetchall()
        for row in rows:
            similarity = _dot(query_embedding, _unpack_embedding(row["query_embedding"]))
            if similarity > best_similarity:
                best_id, best_similarity = row["id"], similarity
                best_answer, best_metadata = row["answer"], (
                    json.loads(row["metadata_json"]) if row["metadata_json"] else None
                )
                best_hit_count = row["hit_count"]

        # Lazy housekeeping
        conn.execute(
            "DELETE FROM semantic_answer_cache WHERE created_at <= ? OR epoch < ?",
            (cutoff, self.epoch),
        )
        self._enforce_cap(conn, config_hash)

    if best_answer is None or best_similarity < self.threshold:
        self.misses += 1
        return None

    with self._connect() as conn:
        conn.execute(
            "UPDATE semantic_answer_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
            (now, best_id),
        )
    self.hits += 1
    return {
        "answer": best_answer,
        "metadata": best_metadata,
        "similarity": best_similarity,
        "hit_count": best_hit_count,
    }
```

Steps:
1. Compute TTL cutoff (`now - ttl_seconds`).
2. SELECT every row with matching `config_hash` AND `epoch` AND `created_at > cutoff`. The composite index makes this O(log n).
3. **Brute-force cosine scan** over all candidates (small N — capped at `max_entries` per config) using `_dot`.
4. Track the best similarity. **No early termination** — every candidate is examined. At ~500 entries × 384 dims = ~200K ops, this is <1 ms even on CPU.
5. **Lazy housekeeping:** in the same transaction, delete expired AND stale-epoch rows. Then enforce the LRU cap.
6. If `best_similarity < threshold` or no candidates → `misses += 1; return None`.
7. Otherwise, in a second transaction, increment `hit_count` and update `last_hit_at`. `hits += 1`. Return the result.

The two-transaction design (read + cleanup in one, hit-count update in another) keeps the long read-and-compare phase isolated from the write.

#### 3.4.6 `store(query_text, query_embedding, config_hash, answer, metadata=None) -> None` — `backend/answer_cache.py:147`

```python
def store(
    self,
    query_text: str,
    query_embedding: Sequence[float],
    config_hash: str,
    answer: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not answer.strip():
        return
    now = self.clock()
    with self._connect() as conn:
        conn.execute(
            "INSERT INTO semantic_answer_cache "
            "(query_text, query_embedding, config_hash, answer, metadata_json, created_at, last_hit_at, hit_count, epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                query_text,
                _pack_embedding(query_embedding),
                config_hash,
                answer,
                json.dumps(metadata) if metadata else None,
                now, now, self.epoch,
            ),
        )
        self._enforce_cap(conn, config_hash)
```

Defensive against empty answers (`if not answer.strip(): return`). Empty answers would otherwise pollute the cache and waste storage.

#### 3.4.7 `_enforce_cap(conn, config_hash) -> None` — `backend/answer_cache.py:176`

LRU eviction scoped to a single `config_hash`. Deletes the oldest `last_hit_at` rows until the count is at or below `max_entries`.

```sql
DELETE FROM semantic_answer_cache WHERE id IN (
    SELECT id FROM semantic_answer_cache
    WHERE config_hash = ? ORDER BY last_hit_at ASC LIMIT ?
)
```

#### 3.4.8 `stats() -> Dict[str, int]` — `backend/answer_cache.py:186`

```python
def stats(self) -> Dict[str, int]:
    with self._connect() as conn:
        entries = self._count(conn, config_hash=None)
    return {"hits": self.hits, "misses": self.misses, "entries": entries}
```

Exposed by the FastAPI `/health` endpoint (`backend/main.py:138`). Note: `hits`/`misses` are **per-process** in-memory counters, not SQL aggregates. Restarting the API resets them.

#### 3.4.9 `_count(conn, config_hash) -> int` — `backend/answer_cache.py:193`

Plain COUNT(*) helper, parameterized on `config_hash` (or all-rows when `None`).

---

## 4. Flow / Lifecycle

### Cache miss (write path)

```
RAGPipeline.stream_response
   │
   ├─► config_hash = sha1(provider|think|rerank|ollama_url|llama_url)
   ├─► query_vec = embeddings.embed_query(prompt)
   ├─► cached = answer_cache.lookup(prompt, query_vec, config_hash)
   │     └─► None
   ├─► retrieve → rerank → build prompt → LLM.stream(...)
   ├─► yield [[METADATA]]…\n + token chunks
   └─► answer_cache.store(prompt, query_vec, config_hash, joined_text, meta_info)
            └─► INSERT INTO semantic_answer_cache ...
            └─► _enforce_cap (delete oldest last_hit_at rows if over max_entries)
```

### Cache hit (read path)

```
RAGPipeline.stream_response
   │
   ├─► config_hash = sha1(...)
   ├─► query_vec = embeddings.embed_query(prompt)
   ├─► cached = answer_cache.lookup(prompt, query_vec, config_hash)
   │     │
   │     ├─► SELECT WHERE config_hash=? AND epoch=? AND created_at>cutoff
   │     ├─► best = max(similarity over candidates)
   │     ├─► if best < threshold: misses+=1; return None
   │     └─► else: hits+=1; return {answer, metadata, similarity, hit_count}
   │
   ├─► cached["answer"] chunked in 32-char slices
   ├─► yield [[METADATA]]{"cache_hit": true, …}\n + chunk + chunk + …
   └─► return   ← no LLM call, no retrieval
```

### Re-index invalidation

```
POST /api/v1/scrape  {reindex: true}
   │
   ▼
backend/main.py:117  rag_pipeline.reload_documents()
   │
   ├─► answer_cache.bump_epoch()         ← epoch +1
   │
   ├─► fetch_opportunity_documents()     ← new corpus
   ├─► _build_parent_docstore()
   ├─► create_child_chunks()
   ├─► FAISS.from_documents(...) + save_local
   └─► BM25Retriever.from_documents(...)
   │
   ▼
   Future lookups match `epoch = new_epoch`, so old entries are invisible.
   Stale-epoch rows are physically cleaned by the next lookup's lazy DELETE.
```

---

## 5. Dependencies

| Import | Used for | Why |
| ------ | -------- | --- |
| `array` | Packing/unpacking embeddings as `array.array('d')` | Stdlib binary format; faster + smaller than JSON. |
| `json` | Serializing `metadata` for storage | Stdlib JSON encoder/decoder. |
| `sqlite3` | Persistent storage | Stdlib; same DB file as the rest of the app. |
| `time` | Default `clock=time.time` for timestamps | Test seam (`clock=FakeClock()`). |
| `typing.{Any, Dict, List, Optional, Sequence}` | Static typing | Public API typing. |

**Zero non-stdlib imports.** This is deliberate — keeps the cache testable in isolation (`tests/test_answer_cache.py`) without spinning up LangChain, sentence-transformers, or any model loaders.

---

## 6. Models & External Services

| Component | Detail |
| --------- | ------ |
| Storage | SQLite (the same `opportunities_chat.db` file used for chat sessions and opportunities). |
| Embeddings | Expected to be **L2-normalized** (set by `HuggingFaceEmbeddings(encode_kwargs={"normalize_embeddings": True})` in `backend/rag.py:344`). Cosine similarity is then a plain dot product. |
| Embedding dimension | Whatever `settings.model.embedding_model` produces (default `intfloat/e5-small-v2` → 384). |

The cache **does not** depend on any specific embedding model — it just stores whatever vector is passed in. Switching embedding models means previous entries become meaningless (you'd want to bump the epoch or wipe the table), but the cache itself doesn't care.

---

## 7. Notable Algorithms

### 7.1 Dot-product-as-cosine

The cosine similarity formula is `dot(a, b) / (||a|| * ||b||)`. For L2-normalized vectors (||a|| = ||b|| = 1), this collapses to `dot(a, b)`. The cache uses `_dot(a, b)` directly (`backend/answer_cache.py:49`), saving two norms per comparison. With 384 dims and up to 500 candidates per lookup, that's ~770K ops saved.

### 7.2 Brute-force linear scan with composite-index prefilter

The composite SQLite index `(config_hash, epoch, created_at)` pre-filters the candidate set before `_dot` runs. In practice, only ~50–200 rows need scoring (TTL-bounded, cap-bounded), so brute force is faster than building an ANN index like FAISS.

### 7.3 Epoch-based bulk invalidation

Rather than physically deleting all entries on re-index, `bump_epoch` increments a counter and lets the WHERE clause in `lookup` naturally filter. Lazy physical deletion happens inside the next `lookup`. This makes re-index O(1) instead of O(n).

### 7.4 Lazy housekeeping

Both `lookup` (lines 124–128) and `store` (line 174) call `_enforce_cap` and the expired-row DELETE. No background sweeper is needed — the cache self-cleans on every read or write. This trades a tiny per-call cost for not needing a separate cron or thread.

### 7.5 Two-transaction write-on-hit

`lookup` reads + cleans up + evicts in one transaction, then updates `hit_count`/`last_hit_at` in a second transaction. This keeps the read phase (the slow part — N dot products) read-only and unblocked, while still letting the write phase be small and fast.

### 7.6 Per-config LRU

The cap is enforced **per `config_hash`**, not globally. This means Ollama's cache and llama.cpp's cache each get up to `max_entries`. Combined with the `last_hit_at` ordering, hot configurations naturally stay populated while cold configurations get evicted first within their bucket.

### 7.7 Test seam via `clock` injection

The constructor accepts a `clock` callable (defaults to `time.time`). Tests in `tests/test_answer_cache.py` inject `FakeClock` to deterministically simulate TTL expiry without `sleep()` calls.

---

## 8. Error Handling

The cache is **exception-light** because it's wrapped in `try/except` at every call site in `backend/rag.py`:

| Site | Behavior |
| ---- | -------- |
| `backend/rag.py:517` | `embeddings.embed_query(prompt)` failure → `cacheable = False`. |
| `backend/rag.py:520–530` | `cache.lookup(...)` failure → falls through to full pipeline. (Currently no try/except in stream_response — relies on `SemanticAnswerCache.lookup` to not raise. The DB connection errors would surface.) |
| `backend/rag.py:689–694` | `cache.store(...)` failure → logged, swallowed. Caching is best-effort and must never break a successful response. |

Inside the class itself:

| Operation | Failure mode |
| --------- | ------------ |
| `lookup` SELECT raises | Propagates up; `stream_response` will raise if the wrapper is added (currently not wrapped, so this would 500). |
| `store` INSERT raises | Propagates; same caveat as above. |
| Empty answer | Silently no-op (`if not answer.strip(): return`). |

Recommendation for hardening: wrap `lookup` and `store` in `try/except` inside `stream_response` to guarantee caching can never break the request.

---

## 9. Notable Patterns & Design Decisions

1. **Pure-stdlib implementation.** No NumPy, no FAISS, no embedding library. This makes the cache unit-testable in microseconds and deployable in minimal environments.

2. **Same DB file as chat sessions.** The cache shares `opportunities_chat.db` with chat history (`backend/database.py:8`). One file to back up, one file to inspect with `sqlite3` CLI. Downside: a corrupt cache could corrupt chat history — but each table is independent.

3. **Cosine-as-dot-product.** Aggressive micro-optimization that pays off because of the repeated `encode_kwargs={"normalize_embeddings": True}` in `backend/rag.py:344`. Documented in the module docstring (`backend/answer_cache.py:4–5`).

4. **Three-layer eviction: TTL + epoch + LRU.** TTL catches stale answers from unchanged corpora; epoch catches stale answers from re-indexed corpora; LRU catches storage growth. Any one would handle most cases; combining all three means the table is bounded in three different dimensions and stays predictable.

5. **Config-hash scoping.** Switching backends (Ollama → llama.cpp) doesn't return stale answers because the SHA1 of the backend config differs. Without this, two different LLMs could serve each other's outputs, which would be confusing.

6. **Lazy housekeeping.** No background thread, no cron, no external sweeper. Cache self-cleans on every operation. Simplifies deployment at the cost of slightly slower reads.

7. **Two-transaction hits.** Read-only prefilter in one transaction (with cleanup), write-on-hit in another. Keeps the dot-product scan from blocking on writes.

8. **No early termination in the scan.** Every candidate is scored. At ~500 candidates × 384 dims this is microseconds; premature optimization would risk correctness for negligible speedup.

9. **JSON metadata side-channel.** The full `meta_info` dict from `stream_response` (including `is_opportunity`, `used_tools`, `initial_docs`, debug info) is round-tripped through `metadata_json`. Cache hits can rebuild the SSE metadata header verbatim.

10. **Brute-force over ANN.** With capped candidate sets, brute force is faster than FAISS for this workload — no index build, no HNSW graph traversal, no serialization. If the cache ever grows past ~5K entries, an FAISS subindex would be the next step.

11. **Stats counters are in-memory, not SQL.** `self.hits` and `self.misses` reset on restart. This is fine for `/health` reporting but means long-term hit-rate analysis would need a separate SQL aggregate.

---

## Cross-references

- Construction site: `backend/rag.py:297` (passed `db_path=DB_PATH` and `settings.model.semantic_cache_*`).
- Call sites: `backend/rag.py:521` (lookup), `backend/rag.py:691` (store).
- Re-index hook: `backend/rag.py:392` (`self.answer_cache.bump_epoch()`).
- Tests: `tests/test_answer_cache.py` (123 lines) — covers hit, near-hit, miss, config scoping, TTL expiry, epoch bump, LRU eviction, hit count, empty-answer skip, metadata round-trip.
- Settings: `config.py:39–43` — `semantic_cache_enabled`, `semantic_cache_similarity_threshold`, `semantic_cache_ttl_hours`, `semantic_cache_max_entries`.