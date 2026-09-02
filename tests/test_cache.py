from kg.ingest.cache import RawCache


def test_put_returns_sha256_and_is_content_addressed(tmp_path):
    cache = RawCache(tmp_path)
    doc_id = cache.put("https://example.com/a.json", b'{"x":1}', "application/json")
    assert len(doc_id) == 64
    assert cache.get(doc_id) == b'{"x":1}'
    assert cache.path_for(doc_id).exists()


def test_identical_content_from_two_uris_stores_once(tmp_path):
    cache = RawCache(tmp_path)
    a = cache.put("https://example.com/a", b"same", "text/plain")
    b = cache.put("https://example.com/b", b"same", "text/plain")
    assert a == b
    blobs = list((tmp_path / "blobs").rglob("*.bin"))
    assert len(blobs) == 1


def test_find_by_uri_survives_a_new_cache_instance(tmp_path):
    RawCache(tmp_path).put("https://example.com/a", b"payload", "text/plain")
    reopened = RawCache(tmp_path)
    doc_id = reopened.find_by_uri("https://example.com/a")
    assert doc_id is not None
    assert reopened.get(doc_id) == b"payload"


def test_find_by_uri_returns_none_when_absent(tmp_path):
    assert RawCache(tmp_path).find_by_uri("https://example.com/missing") is None
