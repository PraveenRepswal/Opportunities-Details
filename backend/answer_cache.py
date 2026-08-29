"""SQLite-backed semantic answer cache.

Serves pre-generated answers for semantically near-identical questions instead
of re-running retrieval + LLM generation. Embeddings are assumed L2-normalized,
so cosine similarity reduces to a plain dot product. Entries are scoped by a
config hash (model/provider flags) and a monotonic epoch that is bumped when
the RAG corpus is re-indexed, plus a TTL backstop and an LRU entry cap.
Pure-stdlib so it can be tested in isolation (see tests/test_answer_cache.py).
"""

import array
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_answer_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    query_embedding BLOB NOT NULL,
    config_hash TEXT NOT NULL,
    answer TEXT NOT NULL,
    metadata_json TEXT,
    created_at REAL NOT NULL,
    last_hit_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    epoch INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sac_lookup ON semantic_answer_cache(config_hash, epoch, created_at);
CREATE TABLE IF NOT EXISTS semantic_answer_cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _pack_embedding(vector: Sequence[float]) -> bytes:
    return array.array("d", vector).tobytes()


def _unpack_embedding(blob: bytes) -> List[float]:
    values = array.array("d")
    values.frombytes(blob)
    return values.tolist()


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class SemanticAnswerCache:
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # -- epoch management -------------------------------------------------

    def _load_epoch(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM semantic_answer_cache_meta WHERE key = 'epoch'"
            ).fetchone()
        return int(row["value"]) if row else 0

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

    # -- core operations ---------------------------------------------------

    def lookup(self, query_text: str, query_embedding: Sequence[float], config_hash: str) -> Optional[Dict[str, Any]]:
        """Return {'answer', 'metadata', 'similarity', 'hit_count'} for the best match above threshold."""
        now = self.clock()
        cutoff = now - self.ttl_seconds
        best_id = None
        best_similarity = -1.0
        best_answer = None
        best_metadata = None
        best_hit_count = 0

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, query_embedding, answer, metadata_json, hit_count FROM semantic_answer_cache "
                "WHERE config_hash = ? AND epoch = ? AND created_at > ?",
                (config_hash, self.epoch, cutoff),
            ).fetchall()
            for row in rows:
                similarity = _dot(query_embedding, _unpack_embedding(row["query_embedding"]))
                if similarity > best_similarity:
                    best_id = row["id"]
                    best_similarity = similarity
                    best_answer = row["answer"]
                    best_metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else None
                    best_hit_count = row["hit_count"]

            # Lazy housekeeping: drop expired / stale-epoch rows and enforce the cap.
            conn.execute(
                "DELETE FROM semantic_answer_cache WHERE created_at <= ? OR epoch < ?", (cutoff, self.epoch)
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
                    now,
                    now,
                    self.epoch,
                ),
            )
            self._enforce_cap(conn, config_hash)

    def _enforce_cap(self, conn: sqlite3.Connection, config_hash: str) -> None:
        """Evict least-recently-hit entries beyond max_entries."""
        excess = self._count(conn, config_hash) - self.max_entries
        if excess > 0:
            conn.execute(
                "DELETE FROM semantic_answer_cache WHERE id IN ("
                "  SELECT id FROM semantic_answer_cache WHERE config_hash = ? ORDER BY last_hit_at ASC LIMIT ?)",
                (config_hash, excess),
            )

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            entries = self._count(conn, config_hash=None)
        return {"hits": self.hits, "misses": self.misses, "entries": entries}

    # -- helpers -------------------------------------------------------------

    def _count(self, conn: sqlite3.Connection, config_hash: Optional[str]) -> int:
        if config_hash is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM semantic_answer_cache").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM semantic_answer_cache WHERE config_hash = ?", (config_hash,)
            ).fetchone()
        return int(row["n"])
