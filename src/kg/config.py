from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG = Path("config/settings.yaml")


class Settings(BaseModel):
    data_root: Path
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str

    @property
    def staging_dir(self) -> Path:
        """Where the parsers write mentions.parquet and edge_mentions.parquet."""
        path = self.data_root / "staging"
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_settings(path: Optional[Path] = None) -> Settings:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/settings.yaml.example to "
            f"config/settings.yaml and fill it in."
        )
    data = yaml.safe_load(path.read_text()) or {}
    known = set(Settings.model_fields)
    return Settings(**{k: v for k, v in data.items() if k in known})
