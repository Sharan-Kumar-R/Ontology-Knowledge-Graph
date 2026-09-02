from neo4j import Driver, GraphDatabase

from kg.config import Settings


def get_driver(settings: Settings) -> Driver:
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def check_connection(driver: Driver) -> dict:
    with driver.session() as session:
        version = session.run(
            "CALL dbms.components() YIELD versions RETURN versions[0] AS v"
        ).single()["v"]
        names = session.run(
            "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s' RETURN count(*) AS c"
        ).single()["c"]
    return {"neo4j_version": version, "n10s_available": names > 0}
