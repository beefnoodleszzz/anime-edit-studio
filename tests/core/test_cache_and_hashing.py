from studio.core.cache import JsonCache
from studio.core.hashing import analysis_cache_key, stable_hash


def test_stable_hash_is_order_independent():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_cache_key_changes_for_every_version_component():
    base = {
        "asset_hash": "a",
        "model": "m",
        "model_version": "1",
        "pipeline_version": "1",
        "parameters": {"x": 1},
    }
    original = analysis_cache_key(**base)
    for field in ("asset_hash", "model", "model_version", "pipeline_version"):
        changed = dict(base)
        changed[field] = "different"
        assert analysis_cache_key(**changed) != original
    changed = dict(base)
    changed["parameters"] = {"x": 2}
    assert analysis_cache_key(**changed) != original


def test_json_cache_roundtrip_is_atomic(tmp_path):
    cache = JsonCache(tmp_path)
    key = "a" * 64
    assert cache.get("visual", key) is None
    path = cache.put("visual", key, {"中文": [1, 2]})
    assert path.is_file()
    assert cache.get("visual", key) == {"中文": [1, 2]}
    assert not list(path.parent.glob(f".{path.name}.*"))
