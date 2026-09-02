from pathlib import Path

import pytest

from kg.config import Settings, load_settings


def test_load_settings_reads_yaml(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "data_root: %s\n"
        "sec_user_agent: 'Test test@example.com'\n"
        "neo4j_password: secret\n" % (tmp_path / "kgdata").as_posix()
    )
    s = load_settings(cfg)
    assert s.sec_user_agent == "Test test@example.com"
    assert s.neo4j_password == "secret"
    assert s.sec_rate_limit == 8.0
    assert s.neo4j_uri == "bolt://localhost:7687"


def test_directories_are_created_on_access(tmp_path):
    s = Settings(
        data_root=tmp_path / "kgdata",
        sec_user_agent="Test test@example.com",
        neo4j_password="secret",
    )
    assert s.raw_dir.is_dir()
    assert s.staging_dir.is_dir()
    assert s.gold_dir.is_dir()


def test_user_agent_must_contain_email(tmp_path):
    with pytest.raises(ValueError, match="contact email"):
        Settings(
            data_root=tmp_path,
            sec_user_agent="just-a-name",
            neo4j_password="secret",
        )
