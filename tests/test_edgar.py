from pathlib import Path

from kg.config import Settings
from kg.ingest.cache import RawCache
from kg.ingest.edgar import (
    cik10,
    fetch_company_tickers,
    fetch_submissions,
    filing_doc_url,
    find_exhibit21,
    find_exhibit21_from_headers,
    recent_filings,
)
from kg.ingest.http import SecClient

FIXTURES = Path(__file__).parent / "fixtures"


def make_client(tmp_path, routes):
    def fake_fetch(url, headers):
        return routes[url], "application/json"

    settings = Settings(
        data_root=tmp_path,
        sec_user_agent="Test test@example.com",
        sec_rate_limit=1000.0,
        neo4j_password="x",
    )
    return SecClient(settings, RawCache(tmp_path / "raw"), fetch=fake_fetch)


def test_cik10_pads():
    assert cik10(320193) == "0000320193"
    assert cik10("320193") == "0000320193"
    assert cik10("0000320193") == "0000320193"


def test_fetch_company_tickers_returns_flat_records(tmp_path):
    url = "https://www.sec.gov/files/company_tickers.json"
    client = make_client(tmp_path, {url: (FIXTURES / "company_tickers.json").read_bytes()})
    doc_id, records = fetch_company_tickers(client)
    assert len(doc_id) == 64
    assert len(records) == 2
    assert {r["ticker"] for r in records} == {"AAPL", "GOOGL"}


def test_recent_filings_filters_by_form(tmp_path):
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    client = make_client(tmp_path, {url: (FIXTURES / "submissions_AAPL.json").read_bytes()})
    _, submissions = fetch_submissions(client, 320193)
    filings = recent_filings(submissions, form="10-K", limit=5)
    assert len(filings) == 1
    assert filings[0]["accession"] == "0000320193-23-000106"
    assert filings[0]["filing_date"] == "2023-11-03"
    assert filings[0]["primary_document"] == "aapl-20230930.htm"


def test_find_exhibit21_matches_ex21_variants():
    index = {"directory": {"item": [
        {"name": "aapl-20230930.htm", "type": "10-K"},
        {"name": "a10-kexhibit211q423.htm", "type": "EX-21.1"},
    ]}}
    assert find_exhibit21(index) == "a10-kexhibit211q423.htm"


def test_find_exhibit21_returns_none_when_absent():
    index = {"directory": {"item": [{"name": "x.htm", "type": "10-K"}]}}
    assert find_exhibit21(index) is None


def test_find_exhibit21_falls_back_to_filename_when_type_is_an_icon():
    index = {"directory": {"item": [
        {"name": "aapl-20250927.htm", "type": "text.gif"},
        {"name": "a10-kexhibit4109272025.htm", "type": "text.gif"},
        {"name": "a10-kexhibit23109272025.htm", "type": "text.gif"},
        {"name": "a10-kexhibit21109272025.htm", "type": "text.gif"},
        {"name": "a10-kexhibit31109272025.htm", "type": "text.gif"},
    ]}}
    assert find_exhibit21(index) == "a10-kexhibit21109272025.htm"


def test_filename_fallback_does_not_confuse_exhibit_23_or_31():
    index = {"directory": {"item": [
        {"name": "a10-kexhibit23109272025.htm", "type": "text.gif"},
        {"name": "a10-kexhibit31209272025.htm", "type": "text.gif"},
    ]}}
    assert find_exhibit21(index) is None


def test_find_exhibit21_from_headers_uses_real_sec_types():
    headers = (
        "&lt;TYPE&gt;EX-4.1\n&lt;SEQUENCE&gt;2\n&lt;FILENAME&gt;a10-kexhibit4109272025.htm\n"
        "&lt;TYPE&gt;EX-21.1\n&lt;SEQUENCE&gt;3\n&lt;FILENAME&gt;a10-kexhibit21109272025.htm\n"
    )
    assert find_exhibit21_from_headers(headers) == "a10-kexhibit21109272025.htm"


def test_find_exhibit21_from_headers_returns_none_when_absent():
    headers = "&lt;TYPE&gt;EX-23.1\n&lt;FILENAME&gt;x.htm\n"
    assert find_exhibit21_from_headers(headers) is None


def test_filing_doc_url_strips_dashes_from_accession():
    url = filing_doc_url(320193, "0000320193-23-000106", "aapl-20230930.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019323000106/aapl-20230930.htm"
    )
