"""Export cached SEC downloads into data/samples/ so the pipeline runs offline.

Run this only when refreshing the bundled sample data. Normal use reads
data/samples/ directly and never touches the network.
"""

import json
import re
import shutil
from pathlib import Path

from kg.config import load_settings
from kg.ingest.cache import RawCache
from kg.parse.semi import DEFAULT_TAGS

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def trim_companyfacts(payload: dict) -> dict:
    """Keep only the XBRL tags the parser actually reads."""
    kept = {
        "cik": payload.get("cik"),
        "entityName": payload.get("entityName"),
        "facts": {},
    }
    for taxonomy, concepts in payload.get("facts", {}).items():
        selected = {k: v for k, v in concepts.items() if k in DEFAULT_TAGS}
        if selected:
            kept["facts"][taxonomy] = selected
    return kept


def main() -> None:
    settings = load_settings()
    cache = RawCache(settings.raw_dir)
    manifest = json.loads((settings.staging_dir / "sec_manifest.json").read_text())

    if SAMPLES.exists():
        shutil.rmtree(SAMPLES)
    for sub in ("structured", "semi/xbrl", "semi/exhibit21", "unstructured"):
        (SAMPLES / sub).mkdir(parents=True, exist_ok=True)

    tickers_doc = (settings.staging_dir / "tickers_doc_id.txt").read_text().strip()
    all_tickers = json.loads(cache.get(tickers_doc))
    ciks = {e["cik"] for e in manifest}
    subset = {k: v for k, v in all_tickers.items() if v["cik_str"] in ciks}
    (SAMPLES / "structured" / "company_tickers.json").write_text(
        json.dumps(subset, indent=1), encoding="utf-8"
    )

    index = []
    for entry in manifest:
        name = slug(entry["title"])
        record = {
            "cik": str(entry["cik"]).zfill(10),
            "title": entry["title"],
            "xbrl": None,
            "exhibit21": None,
            "exhibit21_url": None,
        }
        if entry.get("facts_doc"):
            trimmed = trim_companyfacts(json.loads(cache.get(entry["facts_doc"])))
            path = SAMPLES / "semi" / "xbrl" / f"{name}.json"
            path.write_text(json.dumps(trimmed), encoding="utf-8")
            record["xbrl"] = f"semi/xbrl/{name}.json"
        if entry.get("ex21"):
            path = SAMPLES / "semi" / "exhibit21" / f"{name}.htm"
            path.write_bytes(cache.get(entry["ex21"]["doc_id"]))
            record["exhibit21"] = f"semi/exhibit21/{name}.htm"
            record["exhibit21_url"] = entry["ex21"]["url"]
        index.append(record)

    (SAMPLES / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    total = sum(f.stat().st_size for f in SAMPLES.rglob("*") if f.is_file())
    print(f"exported {len(index)} companies to {SAMPLES}")
    print(f"total size: {total / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
