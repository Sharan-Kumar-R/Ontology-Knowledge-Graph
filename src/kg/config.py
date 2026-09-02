from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

DEFAULT_CONFIG = Path("config/settings.yaml")


class Settings(BaseModel):
    data_root: Path
    sec_user_agent: str
    sec_rate_limit: float = 8.0
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str
    llm_model: str = "claude-sonnet-5"

    @field_validator("sec_user_agent")
    @classmethod
    def _must_have_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError(
                "sec_user_agent must contain a contact email; SEC fair-access "
                "policy rejects requests without one"
            )
        return v

    def _sub(self, name: str) -> Path:
        p = self.data_root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw_dir(self) -> Path:
        return self._sub("raw")

    @property
    def staging_dir(self) -> Path:
        return self._sub("staging")

    @property
    def gold_dir(self) -> Path:
        return self._sub("gold")


def load_settings(path: Optional[Path] = None) -> Settings:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/settings.yaml.example to "
            f"config/settings.yaml and fill it in."
        )
    data = yaml.safe_load(path.read_text()) or {}
    return Settings(**data)
