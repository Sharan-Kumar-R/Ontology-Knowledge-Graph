import time

from kg.config import Settings
from kg.ingest.cache import RawCache
from kg.ingest.http import RateLimiter, SecClient


def make_client(tmp_path, fetch):
    settings = Settings(
        data_root=tmp_path,
        sec_user_agent="Test test@example.com",
        sec_rate_limit=100.0,
        neo4j_password="x",
    )
    return SecClient(settings, RawCache(tmp_path / "raw"), fetch=fetch)


def test_get_bytes_sends_user_agent_and_caches(tmp_path):
    calls = []

    def fake_fetch(url, headers):
        calls.append((url, headers))
        return b"payload", "text/plain"

    client = make_client(tmp_path, fake_fetch)
    doc_id, content = client.get_bytes("https://data.sec.gov/x")
    assert content == b"payload"
    assert calls[0][1]["User-Agent"] == "Test test@example.com"

    doc_id2, content2 = client.get_bytes("https://data.sec.gov/x")
    assert doc_id2 == doc_id
    assert content2 == b"payload"
    assert len(calls) == 1


def test_force_refetches(tmp_path):
    calls = []

    def fake_fetch(url, headers):
        calls.append(url)
        return b"payload", "text/plain"

    client = make_client(tmp_path, fake_fetch)
    client.get_bytes("https://data.sec.gov/x")
    client.get_bytes("https://data.sec.gov/x", force=True)
    assert len(calls) == 2


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(20.0)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - start >= 0.15
