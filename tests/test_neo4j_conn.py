import pytest

from kg.config import load_settings
from kg.load.neo4j_conn import check_connection, get_driver

pytestmark = pytest.mark.integration


def test_neo4j_reachable_with_n10s():
    settings = load_settings()
    driver = get_driver(settings)
    try:
        info = check_connection(driver)
    finally:
        driver.close()
    assert info["neo4j_version"].startswith("5.")
    assert info["n10s_available"] is True
