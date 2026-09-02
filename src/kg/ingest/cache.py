import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class RawCache:
    """Immutable content-addressed store for fetched bytes."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.jsonl"
        self._uri_index: Dict[str, str] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        with self.manifest_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    self._uri_index[rec["uri"]] = rec["doc_id"]

    def path_for(self, doc_id: str) -> Path:
        return self.blobs / doc_id[:2] / f"{doc_id}.bin"

    def put(self, uri: str, content: bytes, content_type: str) -> str:
        doc_id = hashlib.sha256(content).hexdigest()
        path = self.path_for(doc_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        rec = {
            "doc_id": doc_id,
            "uri": uri,
            "content_type": content_type,
            "bytes": len(content),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        self._uri_index[uri] = doc_id
        return doc_id

    def get(self, doc_id: str) -> bytes:
        return self.path_for(doc_id).read_bytes()

    def find_by_uri(self, uri: str) -> Optional[str]:
        return self._uri_index.get(uri)
