"""One-off converter: Exhibit 21 HTML -> JSON.

Run once to produce data/samples/semi/exhibit21/*.json from the original SEC
HTML. The pipeline reads the JSON; this script is kept only to document how
that JSON was derived.
"""

import json
from pathlib import Path

from kg.ingest.local import load_index
from kg.parse.semi import _is_header_row, _rows_from_tables, _rows_from_text
from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parent.parent
EX21 = ROOT / "data" / "samples" / "semi" / "exhibit21"


def extract(html_bytes: bytes) -> dict:
    tree = lxml_html.fromstring(html_bytes)
    rows = [r for r in _rows_from_tables(tree) if not _is_header_row(r[0], r[1])]
    layout = "table"
    if not rows:
        rows = [r for r in _rows_from_text(tree) if not _is_header_row(r[0], r[1])]
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
        html_path = ROOT / "data" / "samples" / entry["exhibit21"]
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
        print(f"  {entry['title'][:30]:<32} {payload['layout']:<10} "
              f"{len(payload['subsidiaries']):>4} subsidiaries")

    index_path = ROOT / "data" / "samples" / "index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nconverted {converted} files, rewrote index.json")


if __name__ == "__main__":
    main()
