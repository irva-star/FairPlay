"""Chokepoint analysis (SPEC section 10).

The claim this view makes: every donor-to-streamer money path ran through
regulated financial nodes, and that is where the case actually broke. CBC
reported that after the documentary aired, banking scrutiny led to the Georgian
account being closed and the site announcing its shutdown.

So: take the money edges, never the unknown ones (a question is not a path),
enumerate every simple path from donors to streamers, and intersect them. A node
on every path is a chokepoint. A chokepoint that is a regulated financial node
is where leverage actually exists, which is a different and more useful finding
than naming a company.
"""

from __future__ import annotations

from dataclasses import dataclass

from contextgraph.load import Graph

SOURCE = "viewers"
TARGET = "streamers"


@dataclass
class Chokepoint:
    node_id: str
    label: str
    kind: str
    regulated: bool

    @property
    def actionable(self) -> bool:
        return self.regulated

    @property
    def tag(self) -> str:
        return "actionable chokepoint" if self.actionable else "chokepoint"


@dataclass
class Analysis:
    paths: list[list[str]]
    chokepoints: list[Chokepoint]      # excludes the two endpoints
    actionable: list[Chokepoint]
    unknowns: list[dict[str, str]]
    endpoints: tuple[str, str] = (SOURCE, TARGET)

    @property
    def every_path_is_regulated(self) -> bool:
        """The headline: is there a regulated node on every single path?"""
        return bool(self.paths) and bool(self.actionable)


def money_paths(graph: Graph, *, source: str = SOURCE, target: str = TARGET) -> list[list[str]]:
    import networkx as nx

    g = nx.DiGraph()
    for node in graph.nodes:
        g.add_node(node.id)
    for edge in graph.money_edges():
        g.add_edge(edge.src, edge.dst, edge_id=edge.id)

    if source not in g or target not in g:
        return []
    return [list(p) for p in nx.all_simple_paths(g, source, target)]


def analyse(graph: Graph, *, source: str = SOURCE, target: str = TARGET) -> Analysis:
    paths = money_paths(graph, source=source, target=target)
    by_id = graph.by_id

    if paths:
        common: set[str] = set(paths[0])
        for path in paths[1:]:
            common &= set(path)
    else:
        common = set()

    # The endpoints sit on every path by definition; they are not chokepoints.
    middle = [n for n in common if n not in (source, target)]
    ordered = [n for n in paths[0] if n in middle] if paths else []

    chokepoints = [
        Chokepoint(node_id=n, label=by_id[n].label, kind=by_id[n].kind,
                   regulated=by_id[n].regulated)
        for n in ordered
    ]

    unknowns = [
        {
            "id": e.id,
            "question": e.question,
            "src": by_id[e.src].label if e.src in by_id else e.src,
            "dst": by_id[e.dst].label if e.dst in by_id else e.dst,
        }
        for e in graph.unknowns()
    ]

    return Analysis(
        paths=paths,
        chokepoints=chokepoints,
        actionable=[c for c in chokepoints if c.actionable],
        unknowns=unknowns,
        endpoints=(source, target),
    )


def summary(analysis: Analysis) -> str:
    if not analysis.paths:
        return "No complete money path from donors to streamers in the reviewed edges."
    lines = [f"{len(analysis.paths)} money path(s) from donors to streamers."]
    for c in analysis.chokepoints:
        lines.append(f"  {c.label} — {c.tag}")
    if analysis.every_path_is_regulated:
        names = ", ".join(c.label for c in analysis.actionable)
        lines.append(f"Every path passes through a regulated node: {names}.")
    lines.append(f"{len(analysis.unknowns)} question(s) public data cannot answer.")
    return "\n".join(lines)
