"""Load and validate the hand-typed graph (SPEC section 10, rules S7-S9).

The loader opens exactly one local file. There is no fetcher and no URL is ever
retrieved: `source_url` is recorded so a reader can check a claim themselves.

The schema is the enforcement point for three rules:
  S8  there is no Person node kind, so a private individual cannot be modelled
      even by accident. CBC named the co-founders; the graph still models only
      the company.
  S9  every claim is one paraphrased sentence, capped in length, and an edge is
      only presented as reviewed once a named human has checked it.
  S7  nothing here is crawled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).parent / "entropy_seed.yaml"
VIEW = "entropy"

# Deliberately no Person kind (S8).
NODE_KINDS = frozenset({
    "Company", "Platform", "Mechanism", "PaymentRail", "Account",
    "Investigation", "AggregateRole",
})

CERTAINTIES = frozenset({
    "journalism_finding", "self_reported_via_journalism", "inferred", "unknown",
})

# A certainty is a line style on the drawing, so the reader can see at a glance
# which claims are journalism and which are a company's own account of itself.
LINE_STYLES = {
    "journalism_finding": "solid",
    "self_reported_via_journalism": "dash",
    "inferred": "dot",
    "unknown": "grey-question",
}

SOURCED_CERTAINTIES = frozenset({"journalism_finding", "self_reported_via_journalism"})

EDGE_KINDS = frozenset({"money", "operates", "pressure", "volume"})

LABEL_MAX = 60
CLAIM_MAX = 200


class GraphError(ValueError):
    """The graph file broke a rule. Refuse to load rather than draw it wrong."""


@dataclass
class Node:
    id: str
    kind: str
    label: str
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def regulated(self) -> bool:
        return bool(self.attrs.get("regulated"))


@dataclass
class Edge:
    id: str
    src: str
    dst: str
    kind: str
    certainty: str
    claim: str = ""
    question: str = ""
    reasoning: str = ""
    source: str = ""
    source_url: str = ""
    reviewed_by: str = ""

    @property
    def reviewed(self) -> bool:
        return bool(self.reviewed_by.strip())

    @property
    def line_style(self) -> str:
        return LINE_STYLES[self.certainty]

    @property
    def is_unknown(self) -> bool:
        return self.certainty == "unknown"


@dataclass
class Graph:
    meta: dict[str, Any]
    sources: dict[str, dict[str, str]]
    nodes: list[Node]
    edges: list[Edge]

    @property
    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def money_edges(self) -> list[Edge]:
        """Money paths only, and never the unknowns: a question is not a path."""
        return [e for e in self.edges if e.kind == "money" and not e.is_unknown]

    def unknowns(self) -> list[Edge]:
        return [e for e in self.edges if e.is_unknown]

    @property
    def unreviewed(self) -> list[Edge]:
        return [e for e in self.edges if not e.reviewed]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GraphError(message)


def parse(data: dict[str, Any]) -> Graph:
    _require(isinstance(data, dict), "seed file is not a mapping")
    sources = data.get("sources") or {}
    _require(isinstance(sources, dict), "sources must be a mapping")

    nodes: list[Node] = []
    seen_nodes: set[str] = set()
    for raw in data.get("nodes") or []:
        attrs = {k: v for k, v in raw.items() if k not in ("id", "kind", "label")}
        node = Node(id=str(raw.get("id", "")), kind=str(raw.get("kind", "")),
                    label=str(raw.get("label", "")), attrs=attrs)
        _require(bool(node.id), "a node is missing its id")
        _require(node.id not in seen_nodes, f"duplicate node id {node.id!r}")
        _require(node.kind in NODE_KINDS,
                 f"node {node.id!r}: kind {node.kind!r} is not allowed. "
                 f"There is deliberately no Person kind (S8).")
        _require(0 < len(node.label) <= LABEL_MAX,
                 f"node {node.id!r}: label must be 1-{LABEL_MAX} characters")
        seen_nodes.add(node.id)
        nodes.append(node)

    edges: list[Edge] = []
    seen_edges: set[str] = set()
    for raw in data.get("edges") or []:
        source_key = str(raw.get("source", ""))
        edge = Edge(
            id=str(raw.get("id", "")), src=str(raw.get("src", "")), dst=str(raw.get("dst", "")),
            kind=str(raw.get("kind", "")), certainty=str(raw.get("certainty", "")),
            claim=str(raw.get("claim", "")), question=str(raw.get("question", "")),
            reasoning=str(raw.get("reasoning", "")), source=source_key,
            source_url=str((sources.get(source_key) or {}).get("url", "")),
            reviewed_by=str(raw.get("reviewed_by", "")),
        )
        _require(bool(edge.id), "an edge is missing its id")
        _require(edge.id not in seen_edges, f"duplicate edge id {edge.id!r}")
        _require(edge.src in seen_nodes, f"edge {edge.id!r}: unknown src {edge.src!r}")
        _require(edge.dst in seen_nodes, f"edge {edge.id!r}: unknown dst {edge.dst!r}")
        _require(edge.kind in EDGE_KINDS, f"edge {edge.id!r}: kind {edge.kind!r} is not allowed")
        _require(edge.certainty in CERTAINTIES,
                 f"edge {edge.id!r}: certainty {edge.certainty!r} is not in {sorted(CERTAINTIES)}")

        if edge.certainty in SOURCED_CERTAINTIES:
            _require(bool(edge.source) and edge.source in sources,
                     f"edge {edge.id!r}: certainty {edge.certainty!r} needs a known source")
            _require(bool(edge.claim), f"edge {edge.id!r}: needs a claim")
        if edge.certainty == "inferred":
            _require(bool(edge.reasoning), f"edge {edge.id!r}: inferred edges need reasoning")
        if edge.certainty == "unknown":
            _require(bool(edge.question), f"edge {edge.id!r}: unknown edges need a question")

        _require(len(edge.claim) <= CLAIM_MAX,
                 f"edge {edge.id!r}: claim must be at most {CLAIM_MAX} characters")
        seen_edges.add(edge.id)
        edges.append(edge)

    return Graph(meta=data.get("meta") or {}, sources=sources, nodes=nodes, edges=edges)


def load(path: str | Path = SEED_PATH) -> Graph:
    import yaml  # lazy, so parse() stays testable without dependencies

    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def to_store(store, graph: Graph, *, view: str = VIEW) -> None:
    for node in graph.nodes:
        store.upsert_node(node.id, view=view, kind=node.kind, label=node.label, attrs=node.attrs)
    for edge in graph.edges:
        store.upsert_edge(
            edge.id, view=view, src=edge.src, dst=edge.dst, kind=edge.kind,
            certainty=edge.certainty, source_url=edge.source_url,
            claim=edge.claim or edge.question, evidence_ref=edge.source,
            reviewed_by=edge.reviewed_by,
        )
