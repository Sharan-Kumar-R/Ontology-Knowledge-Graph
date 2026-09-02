import pytest
from pydantic import ValidationError

from kg.config import Settings, load_settings


def test_load_settings_reads_yaml(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "data_root: %s\nneo4j_password: secret\n" % (tmp_path / "kgdata").as_posix()
    )
    s = load_settings(cfg)
    assert s.neo4j_password == "secret"
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.neo4j_user == "neo4j"


def test_staging_dir_is_created_on_access(tmp_path):
    s = Settings(data_root=tmp_path / "kgdata", neo4j_password="secret")
    assert s.staging_dir.is_dir()
    assert s.staging_dir.name == "staging"


def test_unknown_settings_keys_are_ignored(tmp_path):
    """Older configs carry SEC fields the pipeline no longer uses."""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "data_root: %s\n"
        "neo4j_password: secret\n"
        "sec_user_agent: 'Old Field old@example.com'\n"
        "sec_rate_limit: 8.0\n" % (tmp_path / "kgdata").as_posix()
    )
    s = load_settings(cfg)
    assert s.neo4j_password == "secret"
    assert not hasattr(s, "sec_user_agent")


def test_missing_config_file_says_what_to_do(tmp_path):
    with pytest.raises(FileNotFoundError, match="settings.yaml.example"):
        load_settings(tmp_path / "nope.yaml")


def test_password_is_required(tmp_path):
    with pytest.raises(ValidationError):
        Settings(data_root=tmp_path)
