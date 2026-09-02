"""One-off converter: Exhibit 21 HTML -> JSON."""
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
_FOOTNOTE_MARKER = re.compile(r"^\(?\s*[*†‡\d]+\s*\)?$")

_US_STATES = """
Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware
Florida Georgia Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana
Maine Maryland Massachusetts Michigan Minnesota Mississippi Missouri Montana
Nebraska Nevada Ohio Oklahoma Oregon Pennsylvania Tennessee Texas Utah Vermont
Virginia Washington Wisconsin Wyoming
""".split()

_COUNTRIES = """
Argentina Australia Austria Bahamas Bahrain Bangladesh Barbados Belgium Bermuda
Brazil Bulgaria Canada Chile China Colombia Croatia Cyprus Denmark Ecuador Egypt
Estonia Finland France Germany Gibraltar Greece Guatemala Honduras Hungary
Iceland India Indonesia Ireland Israel Italy Jamaica Japan Jordan Kazakhstan
Kenya Kuwait Latvia Lebanon Liechtenstein Lithuania Luxembourg Malaysia Malta
Mauritius Mexico Monaco Morocco Netherlands Nicaragua Nigeria Norway Oman
Pakistan Panama Paraguay Peru Philippines Poland Portugal Qatar Romania Russia
Serbia Singapore Slovakia Slovenia Spain Sweden Switzerland Taiwan Thailand
Tunisia Turkey Ukraine Uruguay Venezuela Vietnam Zambia Zimbabwe
""".split()

_MULTI_WORD = [
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Rhode Island", "South Carolina", "South Dakota",
    "West Virginia", "District of Columbia", "Puerto Rico", "Cayman Islands",
    "British Virgin Islands", "Channel Islands", "Costa Rica", "Czech Republic",
    "Dominican Republic", "El Salvador", "Hong Kong", "New Zealand",
    "Saudi Arabia", "South Africa", "South Korea", "Sri Lanka",
    "Trinidad and Tobago", "United Arab Emirates", "United Kingdom",
    "United States",
]

JURISDICTIONS = sorted(_US_STATES + _COUNTRIES + _MULTI_WORD, key=len, reverse=True)

_JURISDICTION = re.compile(
    r"\s(" + "|".join(re.escape(j) for j in JURISDICTIONS) + r")"
    r"(?:\s*[,(]\s*U\.?\s?S\.?A?\.?\s*\)?)?(?=\s|$)"
)


def is_header_row(name: str, value: str) -> bool:
    return bool(
        _HEADING.search(name)
        or _HEADING_LABEL.match(name.strip())
        or _HEADING_VALUE.search(value)
    )


def is_footnote_row(name: str) -> bool:
    """A legend entry, not a subsidiary: the name column holds only "(1)"."""
    return bool(_FOOTNOTE_MARKER.match(name.strip()))


def split_run_on(line: str) -> list:
    """Split a run-on paragraph on the jurisdiction that closes each entry."""
    parts = _JURISDICTION.split(line)
    pairs = []
    for i in range(0, len(parts) - 1, 2):
        name = parts[i].lstrip(" .,;").rstrip(" ,;")
        if name:
            pairs.append((name, parts[i + 1]))
    return pairs


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
        line = " ".join(line.split())
        if not line or _HEADING.search(line):
            continue
        if len(_JURISDICTION.findall(line)) >= 2:
            rows.extend(split_run_on(line))
            continue
        parts = [p.strip(" .") for p in _DOTS.split(line) if p.strip(" .")]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def extract(html_bytes: bytes) -> dict:
    def keep(row):
        return not is_header_row(row[0], row[1]) and not is_footnote_row(row[0])

    tree = lxml_html.fromstring(html_bytes)
    rows = [r for r in rows_from_tables(tree) if keep(r)]
    layout = "table"
    if not rows:
        rows = [r for r in rows_from_text(tree) if keep(r)]
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
            html_path = html_path.with_suffix(".htm")
        if not html_path.exists():
            print(f"  skip {entry['title'][:30]:<32} no source HTML")
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
