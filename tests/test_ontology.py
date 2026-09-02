from pathlib import Path

import pytest
from rdflib import OWL, RDF, Graph, URIRef

ONTOLOGY = Path("ontology/ontology.ttl")
SHAPES = Path("ontology/shapes.ttl")
KG = "http://kg.local/sec#"


@pytest.fixture(scope="module")
def onto() -> Graph:
    g = Graph()
    g.parse(ONTOLOGY, format="turtle")
    return g


def test_ontology_and_shapes_parse(onto):
    assert len(onto) > 100
    shapes = Graph()
    shapes.parse(SHAPES, format="turtle")
    assert len(shapes) > 50


def test_parent_of_is_transitive(onto):
    assert (URIRef(KG + "parentOf"), RDF.type, OWL.TransitiveProperty) in onto


def test_direct_parent_of_is_simple_and_subproperty(onto):
    direct = URIRef(KG + "directParentOf")
    assert (direct, RDF.type, OWL.AsymmetricProperty) in onto
    assert (direct, RDF.type, OWL.IrreflexiveProperty) in onto
    assert (direct, URIRef("http://www.w3.org/2000/01/rdf-schema#subPropertyOf"),
            URIRef(KG + "parentOf")) in onto


def test_transitive_property_is_not_also_asymmetric(onto):
    """OWL 2 DL forbids asymmetry and irreflexivity on non-simple roles.

    Declaring parentOf transitive AND asymmetric would push the ontology out of
    OWL 2 DL, and HermiT would refuse to reason over it.
    """
    parent_of = URIRef(KG + "parentOf")
    assert (parent_of, RDF.type, OWL.AsymmetricProperty) not in onto
    assert (parent_of, RDF.type, OWL.IrreflexiveProperty) not in onto


def test_identifies_is_inverse_functional(onto):
    assert (URIRef(KG + "identifies"), RDF.type, OWL.InverseFunctionalProperty) in onto


def test_core_classes_are_declared(onto):
    declared = {str(c).split("#")[-1] for c in onto.subjects(RDF.type, OWL.Class)}
    assert {"LegalEntity", "Person", "Identifier", "FinancialFact"} <= declared


def test_disjointness_is_declared(onto):
    assert list(onto.subjects(RDF.type, OWL.AllDisjointClasses))


def test_ontology_is_consistent_under_hermit():
    owlready2 = pytest.importorskip("owlready2")
    owl_file = Path("ontology/ontology.owl")
    if not owl_file.exists():
        pytest.skip("run ontology/build.py first")
    onto = owlready2.get_ontology("file://" + owl_file.resolve().as_posix()).load()
    with onto:
        owlready2.sync_reasoner(debug=0)
    assert not list(owlready2.default_world.inconsistent_classes())
