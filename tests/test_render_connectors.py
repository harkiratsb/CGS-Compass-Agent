"""Lettered in-page connectors never run out: A..Z (no I/O), then AA, AB, ..."""
from itertools import islice

from render.drawio import render_drawio
from render.swimlane import build_geometry, connector_letters, render_svg
from schema import Activity, Edge, ProcessMeta, ProcessModel, Role


def _many_jumps(n: int) -> ProcessModel:
    """n steps alternating between two lanes, each with a backward jump across lanes."""
    roles = [Role(id="R1", name="Lane one"), Role(id="R2", name="Lane two")]
    acts = [Activity(id="S", name="Start", role_id="R1", kind="start", order=0)]
    acts += [Activity(id=f"A{i}", name=f"Do step {i}", role_id="R1" if i % 2 else "R2", order=i)
             for i in range(1, n + 1)]
    acts.append(Activity(id="E", name="End", role_id="R1", kind="end", order=n + 1))
    edges = [Edge(from_id="S", to_id="A1"), Edge(from_id=f"A{n}", to_id="E")]
    edges += [Edge(from_id=f"A{i}", to_id=f"A{i + 1}") for i in range(1, n)]
    edges += [Edge(from_id=f"A{i}", to_id=f"A{i - 1}", label="redo") for i in range(2, n + 1)]
    return ProcessModel(process=ProcessMeta(name="Many jumps"), roles=roles, activities=acts, edges=edges)


def test_letter_sequence_continues_past_z():
    seq = list(islice(connector_letters(), 24 + 24 * 24 + 1))
    assert seq[:3] == ["A", "B", "C"] and seq[23:26] == ["Z", "AA", "AB"]
    assert seq[-1] == "AAA" and len(set(seq)) == len(seq)
    assert not any(c in s for s in seq for c in "IO")


def test_more_than_thirty_lettered_connectors_render():
    m = _many_jumps(36)
    g = build_geometry(m)
    letters = [e.letter for e in g.edges if e.letter]
    assert len(letters) > 30 and len(set(letters)) == len(letters)
    assert "AA" in letters
    svg = render_svg(m, geometry=g)
    assert ">AA<" in svg and "AA:" in svg       # drawn on the map and listed in the legend
    render_drawio(m, g)                          # draw.io draws these as real edges; must not fail
