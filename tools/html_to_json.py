"""One-off converter: Exhibit 21 HTML -> JSON.

SEC filers publish Exhibit 21 in two shapes: a real HTML table, or indented
free text with dot leaders. This script handles both, drops header rows, and
writes the result to data/samples/semi/exhibit21/*.json, which is what the
pipeline reads.

Run it only when refreshing the bundled data. It is kept in the repo so the
derivation of those JSON files is auditable.
"""

import json
import re
from pathlib import Path

from kg.ingest.local import load_index
from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"

_DOTS = re.compile(r"\.{3,}|\s{3,}|\t+")
_HEADING = re.compile(r"subsidiar|jurisdiction|name of|state of|registrant", re.I)
_HEADING_LABEL = re.compile(
    r"^(name|entity|entity name|company|subsidiary|legal name)$", re.I
)
_HEADING_VALUE = re.compile(
    r"incorporat|jurisdiction|domicile|organiz|state or|country|location", re.I
)


def is_header_row(name: str, value: str) -> bool:
    return bool(
        _HEADING.search(name)
        or _HEADING_LABEL.match(name.strip())
        or _HEADING_VALUE.search(value)
    )


def rows_from_tables(tree) -> list:
    """Take cells by position, never by tag name - filers style them freely."""
    rows = []
    for table in tree.xpath("//table"):
        for tr in table.xpath(".//tr"):
            cells = [
                " ".join(td.text_content().split()) for td in tr.xpath("./td|./th")
            ]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                rows.append((cells[0], cells[1]))
    return rows


def rows_from_text(tree) -> list:
    """Fallback for filers who lay the list out as text with dot leaders."""
    rows = []
    for line in tree.text_content().splitlines():
        line = line.strip()
        if not line or _HEADING.search(line):
            continue
        parts = [p.strip(" .") for p in _DOTS.split(line) if p.strip(" .")]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def extract(html_bytes: bytes) -> dict:
    tree = lxml_html.fromstring(html_bytes)
    rows = [r for r in rows_from_tables(tree) if not is_header_row(r[0], r[1])]
    layout = "table"
    if not rows:
        rows = [r for r in rows_from_text(tree) if not is_header_row(r[0], r[1])]
        layout = "free_text"
    return {
        "layout": layout,
        "subsidiaries": [
            {"name": name, "jurisdiction_text": jurisdiction}
            for name, jurisdiction in rows
        ],
    }


def main() -> None:
    index = load_index()
    converted = 0
    for entry in index:
        if not entry.get("exhibit21"):
            continue
        html_path = SAMPLES / entry["exhibit21"]
        if html_path.suffix.lower() not in (".htm", ".html"):
            print(f"  skip {entry['title'][:30]:<32} already converted")
            continue
        if not html_path.exists():
            continue
        payload = extract(html_path.read_bytes())
        payload["parent_name"] = entry["title"]
        payload["parent_cik"] = entry["cik"]
        payload["source_url"] = entry["exhibit21_url"]
        payload["source_file"] = html_path.name

        json_path = html_path.with_suffix(".json")
        json_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        entry["exhibit21"] = f"semi/exhibit21/{json_path.name}"
        converted += 1
        print(
            f"  {entry['title'][:30]:<32} {payload['layout']:<10} "
            f"{len(payload['subsidiaries']):>4} subsidiaries"
        )

    (SAMPLES / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nconverted {converted} files, rewrote index.json")


if __name__ == "__main__":
    main()
