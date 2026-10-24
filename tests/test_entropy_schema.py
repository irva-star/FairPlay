"""Rules S8 and S9: the graph schema, and the chokepoint analysis.

S8 is enforced by there being no Person node kind at all. CBC named the
co-founders; the graph still models only the company, and the schema is what
makes that hold even if someone adds an edge in a hurry.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from contextgraph.load import (
    CERTAINTIES, CLAIM_MAX, LABEL_MAX, LINE_STYLES, NODE_KINDS, GraphError, parse,
)

BASE = {
    "meta": {"case": "test", "rules": "Paraphrase only."},
    "sources": {"s1": {"url": "https://example.org/a", "outlet": "CBC News"}},
    "nodes": [
        {"id": "viewers", "kind": "AggregateRole", "label": "Viewers"},
        {"id": "platform", "kind": "Platform", "label": "Platform"},
        {"id": "rail", "kind": "PaymentRail", "label": "Processor", "regulated": True},
        {"id": "acct", "kind": "Account", "label": "Offshore account", "regulated": True},
        {"id": "streamers", "kind": "AggregateRole", "label": "Streamers"},
    ],
    "edges": [
        {"id": "e1", "src": "viewers", "dst": "platform", "kind": "money",
         "certainty": "journalism_finding", "source": "s1", "claim": "Viewers paid.",
         "reviewed_by": "Curator"},
        {"id": "e2", "src": "platform", "dst": "rail", "kind": "money",
         "certainty": "self_reported_via_journalism", "source": "s1",
         "claim": "Funds moved through a processor.", "reviewed_by": "Curator"},
        {"id": "e3", "src": "rail", "dst": "acct", "kind": "money",
         "certainty": "journalism_finding", "source": "s1", "claim": "Held offshore.",
         "reviewed_by": "Curator"},
        {"id": "e4", "src": "acct", "dst": "streamers", "kind": "money",
         "certainty": "journalism_finding", "source": "s1", "claim": "Paid out.",
         "reviewed_by": "Curator"},
        {"id": "u1", "src": "viewers", "dst": "streamers", "kind": "money",
         "certainty": "unknown", "question": "How much did each streamer receive?",
         "reviewed_by": ""},
    ],
}


def mutated(**changes):
    data = copy.deepcopy(BASE)
    for key, fn in changes.items():
        fn(data)
    return data


def test_the_sample_graph_parses() -> None:
    graph = parse(copy.deepcopy(BASE))
    assert len(graph.nodes) == 5
    assert len(graph.edges) == 5
    assert len(graph.money_edges()) == 4, "the unknown edge is not a money path"
    assert len(graph.unknowns()) == 1


def test_there_is_no_person_node_kind() -> None:
    assert "Person" not in NODE_KINDS
    assert NODE_KINDS == {"Company", "Platform", "Mechanism", "PaymentRail",
                          "Account", "Investigation", "AggregateRole"}


@pytest.mark.parametrize("kind", ["Person", "Individual", "User", "Streamer", "person"])
def test_person_like_node_kinds_are_rejected(kind: str) -> None:
    data = mutated(nodes=lambda d: d["nodes"].append(
        {"id": "p", "kind": kind, "label": "Someone"}))
    with pytest.raises(GraphError, match="not allowed"):
        parse(data)


def test_unknown_edges_need_a_question() -> None:
    data = mutated(edges=lambda d: d["edges"].append(
        {"id": "u2", "src": "rail", "dst": "acct", "kind": "volume", "certainty": "unknown"}))
    with pytest.raises(GraphError, match="need a question"):
        parse(data)


def test_inferred_edges_need_reasoning() -> None:
    data = mutated(edges=lambda d: d["edges"].append(
        {"id": "i1", "src": "rail", "dst": "acct", "kind": "volume", "certainty": "inferred"}))
    with pytest.raises(GraphError, match="need reasoning"):
        parse(data)


def test_sourced_edges_need_a_known_source() -> None:
    with pytest.raises(GraphError, match="needs a known source"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"source": "nonexistent"})))
    with pytest.raises(GraphError, match="needs a known source"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"source": ""})))


def test_sourced_edges_need_a_claim() -> None:
    with pytest.raises(GraphError, match="needs a claim"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"claim": ""})))


@pytest.mark.parametrize("certainty", ["vibes", "probably", "", "TRUE", "fact"])
def test_certainty_must_be_in_the_enum(certainty: str) -> None:
    with pytest.raises(GraphError, match="certainty"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"certainty": certainty})))


def test_every_certainty_has_a_line_style() -> None:
    assert set(LINE_STYLES) == CERTAINTIES


def test_labels_and_claims_are_capped() -> None:
    """S9: each claim is one paraphrased sentence, not a quoted passage."""
    with pytest.raises(GraphError, match="label must be"):
        parse(mutated(nodes=lambda d: d["nodes"][0].update({"label": "x" * (LABEL_MAX + 1)})))
    with pytest.raises(GraphError, match="at most"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"claim": "y" * (CLAIM_MAX + 1)})))


def test_dangling_edges_are_rejected() -> None:
    with pytest.raises(GraphError, match="unknown dst"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"dst": "ghost"})))
    with pytest.raises(GraphError, match="unknown src"):
        parse(mutated(edges=lambda d: d["edges"][0].update({"src": "ghost"})))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(GraphError, match="duplicate node"):
        parse(mutated(nodes=lambda d: d["nodes"].append(
            {"id": "viewers", "kind": "Company", "label": "Dup"})))
    with pytest.raises(GraphError, match="duplicate edge"):
        parse(mutated(edges=lambda d: d["edges"].append(copy.deepcopy(d["edges"][0]))))


def test_unreviewed_edges_are_reported_as_drafts() -> None:
    graph = parse(mutated(edges=lambda d: d["edges"][0].update({"reviewed_by": ""})))
    assert [e.id for e in graph.unreviewed] == ["e1", "u1"]
    assert not graph.by_id["viewers"].regulated


# --- the real seed file -------------------------------------------------------

def test_the_shipped_seed_file_is_valid() -> None:
    pytest.importorskip("yaml")
    from contextgraph.load import load

    graph = load()
    assert graph.nodes and graph.edges
    for node in graph.nodes:
        assert node.kind in NODE_KINDS


def test_the_shipped_seed_file_names_no_individuals() -> None:
    """Belt and braces on S8/S9: no node is a person, and no claim quotes one."""
    pytest.importorskip("yaml")
    from contextgraph.load import load

    graph = load()
    assert all(node.kind != "Person" for node in graph.nodes)
    text = Path("contextgraph/entropy_seed.yaml").read_text(encoding="utf-8")
    assert "kind: Person" not in text
    # No quoted passages: claims are paraphrase only.
    for edge in graph.edges:
        assert '"' not in edge.claim
        assert "@" not in edge.claim


# --- chokepoints --------------------------------------------------------------

def test_chokepoints_are_the_nodes_on_every_money_path() -> None:
    pytest.importorskip("networkx")
    from contextgraph.analyze import analyse

    analysis = analyse(parse(copy.deepcopy(BASE)))
    assert analysis.paths == [["viewers", "platform", "rail", "acct", "streamers"]]
    ids = [c.node_id for c in analysis.chokepoints]
    assert ids == ["platform", "rail", "acct"]
    assert "viewers" not in ids, "an endpoint is not a chokepoint"
    assert "streamers" not in ids


def test_regulated_chokepoints_are_labelled_actionable() -> None:
    pytest.importorskip("networkx")
    from contextgraph.analyze import analyse

    analysis = analyse(parse(copy.deepcopy(BASE)))
    assert [c.node_id for c in analysis.actionable] == ["rail", "acct"]
    assert all(c.tag == "actionable chokepoint" for c in analysis.actionable)
    assert analysis.every_path_is_regulated


def test_unknown_edges_never_create_a_path() -> None:
    """u1 goes viewers -> streamers directly; if it counted, nothing would be a
    chokepoint, and the whole finding would evaporate."""
    pytest.importorskip("networkx")
    from contextgraph.analyze import analyse

    analysis = analyse(parse(copy.deepcopy(BASE)))
    assert len(analysis.paths) == 1
    assert analysis.chokepoints


def test_a_second_route_narrows_the_chokepoints() -> None:
    pytest.importorskip("networkx")
    from contextgraph.analyze import analyse

    data = copy.deepcopy(BASE)
    data["nodes"].append({"id": "rail2", "kind": "PaymentRail", "label": "Second processor"})
    data["edges"].append({"id": "e5", "src": "platform", "dst": "rail2", "kind": "money",
                          "certainty": "journalism_finding", "source": "s1",
                          "claim": "A second processor.", "reviewed_by": "C"})
    data["edges"].append({"id": "e6", "src": "rail2", "dst": "acct", "kind": "money",
                          "certainty": "journalism_finding", "source": "s1",
                          "claim": "Also to the account.", "reviewed_by": "C"})
    analysis = analyse(parse(data))
    assert len(analysis.paths) == 2
    ids = [c.node_id for c in analysis.chokepoints]
    assert "rail" not in ids, "a bypassable node is not a chokepoint"
    assert ids == ["platform", "acct"]


def test_the_shipped_graph_shows_a_regulated_chokepoint() -> None:
    """The claim the second view actually makes."""
    pytest.importorskip("yaml")
    pytest.importorskip("networkx")
    from contextgraph.analyze import analyse
    from contextgraph.load import load

    analysis = analyse(load())
    assert analysis.paths, "there should be at least one donor-to-streamer path"
    assert analysis.every_path_is_regulated
    assert any(c.node_id == "account_ge" for c in analysis.actionable)
