import json
import re
from typing import List, Optional, Tuple

from kg.ingest.http import SecClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{filename}"
HEADERS_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{accession}-index-headers.html"
)


def cik10(cik) -> str:
    return str(int(cik)).zfill(10)


def fetch_company_tickers(client: SecClient) -> Tuple[str, List[dict]]:
    doc_id, content = client.get_bytes(TICKERS_URL)
    payload = json.loads(content)
    return doc_id, list(payload.values())


def fetch_submissions(client: SecClient, cik) -> Tuple[str, dict]:
    doc_id, content = client.get_bytes(SUBMISSIONS_URL.format(cik=cik10(cik)))
    return doc_id, json.loads(content)


def fetch_companyfacts(client: SecClient, cik) -> Tuple[str, dict]:
    doc_id, content = client.get_bytes(COMPANYFACTS_URL.format(cik=cik10(cik)))
    return doc_id, json.loads(content)


def fetch_filing_index(client: SecClient, cik, accession: str) -> Tuple[str, dict]:
    url = INDEX_URL.format(cik=int(cik), acc=accession.replace("-", ""))
    doc_id, content = client.get_bytes(url)
    return doc_id, json.loads(content)


def recent_filings(submissions: dict, form: str = "10-K", limit: int = 5) -> List[dict]:
    recent = submissions["filings"]["recent"]
    out = []
    for i, f in enumerate(recent["form"]):
        if f != form:
            continue
        out.append(
            {
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "primary_document": recent["primaryDocument"][i],
                "form": f,
            }
        )
        if len(out) >= limit:
            break
    return out


EX21_FILENAME = re.compile(r"ex(?:hibit)?[-_. ]?21", re.I)
HEADER_DOC = re.compile(
    r"&lt;TYPE&gt;\s*(?P<type>[^\s<&]+).*?&lt;FILENAME&gt;\s*(?P<name>[^\s<&]+)",
    re.S,
)


def find_exhibit21(index_json: dict) -> Optional[str]:
    """Locate the Exhibit 21 document in a filing's index.json.

    EDGAR's index.json ``type`` field carries an icon name (``text.gif``), not
    the SEC document type, so the filename pattern is the reliable signal here.
    Use :func:`find_exhibit21_from_headers` when authoritative types matter.
    """
    items = index_json.get("directory", {}).get("item", [])
    for item in items:
        if item.get("type", "").upper().startswith("EX-21"):
            return item["name"]
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith((".htm", ".html", ".txt")) and EX21_FILENAME.search(name):
            return name
    return None


def fetch_filing_headers(client: SecClient, cik, accession: str) -> Tuple[str, str]:
    url = HEADERS_URL.format(
        cik=int(cik), acc=accession.replace("-", ""), accession=accession
    )
    doc_id, content = client.get_bytes(url)
    return doc_id, content.decode("utf-8", "ignore")


def find_exhibit21_from_headers(headers_text: str) -> Optional[str]:
    """Locate Exhibit 21 using the authoritative SEC document types."""
    for match in HEADER_DOC.finditer(headers_text):
        if match.group("type").upper().startswith("EX-21"):
            return match.group("name")
    return None


def filing_doc_url(cik, accession: str, filename: str) -> str:
    return DOC_URL.format(
        cik=int(cik), acc=accession.replace("-", ""), filename=filename
    )
