"""Minimal Neo4j client over the HTTP Query API, for networks that block 7687."""

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Optional

from kg.config import Settings

CA_BUNDLE_ENV = "KG_CA_BUNDLE"


def build_ssl_context() -> ssl.SSLContext:
    """Trust the OS certificate store, so corporate TLS interception verifies."""
    bundle = os.environ.get(CA_BUNDLE_ENV)
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


class HttpError(RuntimeError):
    pass


class Record(dict):
    """One result row, addressed by field name like a bolt record."""


class Result(list):
    """All rows of one statement."""

    def single(self) -> Optional[Record]:
        return self[0] if self else None

    def data(self) -> list:
        return list(self)


class HttpSession:
    def __init__(self, driver: "HttpDriver"):
        self._driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **parameters) -> Result:
        return self._driver.execute(query, parameters)

    def close(self) -> None:
        pass


class HttpDriver:
    """Speaks the subset of the bolt Driver surface that check, load and stats use."""

    def __init__(self, uri: str, auth: tuple, database: str, timeout: int = 120):
        host = uri.split("://", 1)[-1].rstrip("/")
        self.url = f"https://{host}/db/{database}/query/v2"
        self.timeout = timeout
        self.context = build_ssl_context()
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def execute(self, query: str, parameters: Optional[dict] = None) -> Result:
        payload = {"statement": query}
        if parameters:
            payload["parameters"] = parameters
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=self.headers
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self.context
            ) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise HttpError(f"{exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}")

        if body.get("errors"):
            raise HttpError(json.dumps(body["errors"])[:400])

        data = body.get("data") or {}
        fields = data.get("fields") or []
        return Result(Record(zip(fields, row)) for row in data.get("values") or [])

    def session(self, **kwargs) -> HttpSession:
        return HttpSession(self)

    def close(self) -> None:
        pass


def get_http_driver(settings: Settings, database: Optional[str] = None) -> HttpDriver:
    """Build an HTTP driver, defaulting the database name to the Aura instance id."""
    return HttpDriver(
        settings.neo4j_uri,
        (settings.neo4j_user, settings.neo4j_password),
        database or settings.neo4j_user,
    )
