"""Tests de la caché exact-match: clave, caducidad y expulsión LRU."""

from app.services.cache import ExactMatchCache

REQUEST = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "max_tokens": 4000,
    "system": "Eres un estimador.",
    "messages": [{"role": "user", "content": "landing page"}],
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_same_request_same_key() -> None:
    reordered = dict(reversed(list(REQUEST.items())))
    assert ExactMatchCache.make_key(**REQUEST) == ExactMatchCache.make_key(**reordered)


def test_any_change_changes_key() -> None:
    key = ExactMatchCache.make_key(**REQUEST)
    for field, value in [
        ("provider", "anthropic"),
        ("model", "gpt-4o"),
        ("system", "Otro prompt"),
        ("messages", [{"role": "user", "content": "landing page."}]),
        ("max_tokens", 100),
    ]:
        assert ExactMatchCache.make_key(**(REQUEST | {field: value})) != key, field


def test_hit_and_miss_counters() -> None:
    cache = ExactMatchCache(ttl_seconds=60, max_entries=10)
    assert cache.get("k") is None
    cache.set("k", {"estimation": "x"})
    assert cache.get("k") == {"estimation": "x"}
    assert (cache.hits, cache.misses) == (1, 1)


def test_returned_value_is_a_copy() -> None:
    cache = ExactMatchCache(ttl_seconds=60, max_entries=10)
    cache.set("k", {"estimation": "x"})
    cache.get("k")["estimation"] = "modificado"
    assert cache.get("k") == {"estimation": "x"}


def test_entries_expire_after_ttl() -> None:
    clock = FakeClock()
    cache = ExactMatchCache(ttl_seconds=60, max_entries=10, clock=clock)
    cache.set("k", {"estimation": "x"})
    clock.now = 59
    assert cache.get("k") is not None
    clock.now = 60
    assert cache.get("k") is None
    assert len(cache) == 0


def test_least_recently_used_is_evicted() -> None:
    cache = ExactMatchCache(ttl_seconds=60, max_entries=2)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    cache.get("a")  # "a" pasa a ser la más reciente
    cache.set("c", {"v": 3})
    assert cache.get("b") is None
    assert cache.get("a") == {"v": 1}
    assert cache.get("c") == {"v": 3}
