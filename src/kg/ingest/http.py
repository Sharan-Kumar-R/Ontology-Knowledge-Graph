import threading
import time
from typing import Callable, Optional, Tuple

import httpx

from kg.config import Settings
from kg.ingest.cache import RawCache

FetchFn = Callable[[str, dict], Tuple[bytes, str]]


class RateLimiter:
    """Blocks so calls are spaced at least 1/rate seconds apart."""

    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec
        self._last = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


def _httpx_fetch(url: str, headers: dict) -> Tuple[bytes, str]:
    response = httpx.get(url, headers=headers, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "")


class SecClient:
    """Rate-limited, cache-backed fetcher for SEC endpoints."""

    def __init__(
        self,
        settings: Settings,
        cache: RawCache,
        fetch: Optional[FetchFn] = None,
    ):
        self.settings = settings
        self.cache = cache
        self.limiter = RateLimiter(settings.sec_rate_limit)
        self._fetch = fetch or _httpx_fetch

    def get_bytes(self, url: str, force: bool = False) -> Tuple[str, bytes]:
        if not force:
            doc_id = self.cache.find_by_uri(url)
            if doc_id is not None:
                return doc_id, self.cache.get(doc_id)
        self.limiter.acquire()
        headers = {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        content, content_type = self._fetch(url, headers)
        doc_id = self.cache.put(url, content, content_type)
        return doc_id, content
