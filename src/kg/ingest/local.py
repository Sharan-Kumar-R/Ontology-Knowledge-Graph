"""Read the bundled sample data in data/samples/ instead of fetching from SEC.

This is the default path. Everything the pipeline needs ships with the repo,
so it runs offline with no SEC contact email and no network access.
"""

import json
from pathlib import Path
from typing import Optional

SAMPLES = Path("data/samples")


def sample_root(root: Optional[Path] = None) -> Path:
    root = Path(root) if root else SAMPLES
    if not root.exists():
        raise FileNotFoundError(
            f"{root} not found. Run from the project root, or regenerate it "
            f"with: python scripts/export_samples.py"
        )
    return root


def load_index(root: Optional[Path] = None) -> list:
    """The list of companies in the sample set."""
    return json.loads((sample_root(root) / "index.json").read_text(encoding="utf-8"))


def load_tickers(root: Optional[Path] = None) -> list:
    """Company/ticker records, flattened the same way the SEC feed is."""
    path = sample_root(root) / "structured" / "company_tickers.json"
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def load_xbrl(entry: dict, root: Optional[Path] = None) -> Optional[dict]:
    if not entry.get("xbrl"):
        return None
    path = sample_root(root) / entry["xbrl"]
    return json.loads(path.read_text(encoding="utf-8"))


def load_exhibit21(entry: dict, root: Optional[Path] = None) -> Optional[bytes]:
    if not entry.get("exhibit21"):
        return None
    return (sample_root(root) / entry["exhibit21"]).read_bytes()


def doc_id_for(relative_path: str) -> str:
    """A stable pseudo source_doc id for a bundled file.

    Real fetches hash their content. Sample files keep a deterministic
    64-character id derived from the path so provenance stays populated and
    SHACL's source-document pattern still passes.
    """
    import hashlib

    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
