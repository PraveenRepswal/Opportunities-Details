"""Tests for the SQLite-backed semantic answer cache (pure stdlib module)."""

import pytest

from backend.answer_cache import SemanticAnswerCache


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


CFG = "config-hash-1"
THRESHOLD = 0.9

# Unit vectors: v_similar has cosine 0.995 vs V_BASE; v_dissimilar has 0.
V_BASE = [1.0, 0.0]
V_SIMILAR = [0.995, 0.0998749]
V_DISSIMILAR = [0.0, 1.0]


@pytest.fixture
def cache(tmp_path):
    return SemanticAnswerCache(
        db_path=tmp_path / "cache.db",
        threshold=THRESHOLD,
        ttl_hours=24,
        max_entries=3,
        clock=FakeClock(),
    )


def test_store_then_exact_hit(cache):
    cache.store("What scholarships exist?", V_BASE, CFG, "Cached answer body.")
    result = cache.lookup("What scholarships exist?", V_BASE, CFG)
    assert result is not None
    assert result["answer"] == "Cached answer body."
    assert result["similarity"] >= THRESHOLD
    assert cache.hits == 1 and cache.misses == 0


def test_semantically_near_query_hits(cache):
    cache.store("What scholarships exist?", V_BASE, CFG, "Answer A")
    result = cache.lookup("scholarships that exist?", V_SIMILAR, CFG)
    assert result is not None and result["answer"] == "Answer A"


def test_below_threshold_misses(cache):
    cache.store("PhD funding in Germany", V_BASE, CFG, "German PhD answer")
    assert cache.lookup("Bachelors in Canada", V_DISSIMILAR, CFG) is None
    assert cache.misses == 1


def test_config_hash_scopes_entries(cache):
    cache.store("same question", V_BASE, "hash-ollama", "Ollama answer")
    assert cache.lookup("same question", V_BASE, "hash-llamacpp") is None


def test_ttl_expiry(cache):
    cache.store("question", V_BASE, CFG, "stale answer")
    cache.clock.advance(24 * 3600 + 1)
    assert cache.lookup("question", V_BASE, CFG) is None


def test_bump_epoch_invalidates_all_entries(cache):
    cache.store("q1", V_BASE, CFG, "old corpus answer")
    cache.bump_epoch()
    assert cache.epoch == 1
    assert cache.lookup("q1", V_BASE, CFG) is None

    # New entries under the new epoch are served again.
    cache.store("q2", V_BASE, CFG, "new corpus answer")
    assert cache.lookup("q2", V_BASE, CFG)["answer"] == "new corpus answer"


def test_lru_eviction_respects_max_entries(cache):
    clock = cache.clock
    # Distinct near-orthogonal vectors so each lookup can only match its own entry.
    vecs = [[1.0, 0.0], [0.7071, 0.7071], [0.0, 1.0], [-0.7071, 0.7071]]

    cache.store("q1", vecs[0], CFG, "a1")
    clock.advance(10)
    cache.store("q2", vecs[1], CFG, "a2")
    clock.advance(10)
    cache.store("q3", vecs[2], CFG, "a3")

    # Touch q1 so it becomes most-recently-used, leaving q2 as the victim.
    clock.advance(10)
    cache.lookup("q1", vecs[0], CFG)
    clock.advance(10)
    cache.store("q4", vecs[3], CFG, "a4")

    assert cache.stats()["entries"] == 3
    assert cache.lookup("q2", vecs[1], CFG) is None
    assert cache.lookup("q1", vecs[0], CFG)["answer"] == "a1"
    assert cache.lookup("q4", vecs[3], CFG)["answer"] == "a4"


def test_hit_count_increments_per_hit(cache):
    cache.store("q", V_BASE, CFG, "answer")
    first = cache.lookup("q", V_BASE, CFG)
    second = cache.lookup("q", V_BASE, CFG)
    assert first["hit_count"] == 0 and second["hit_count"] == 1


def test_empty_answers_are_not_stored(cache):
    cache.store("q", V_BASE, CFG, "   ")
    assert cache.lookup("q", V_BASE, CFG) is None
    assert cache.stats() == {"hits": 0, "misses": 1, "entries": 0}


def test_metadata_roundtrip(tmp_path):
    cache = SemanticAnswerCache(db_path=tmp_path / "cache.db", threshold=THRESHOLD, clock=FakeClock())
    meta = {"is_opportunity": True, "used_tools": ["search_local_opportunities"], "initial_docs": ["Doc A"]}
    cache.store("q", V_BASE, CFG, "answer", metadata=meta)
    result = cache.lookup("q", V_BASE, CFG)
    assert result["metadata"] == meta
