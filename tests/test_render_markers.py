import re
import xml.etree.ElementTree as ET
from pathlib import Path

from render.drawio import render_drawio
from render.swimlane import build_geometry, render_svg

FIX = Path(__file__).parent / "fixtures"
MARKS = {"A2": "rules", "A6": "agent", "A11": "approval", "D1": "removed"}


def _cells(xml):
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell")}


def _style(xml, node_id):
    return _cells(xml)[f"n_{node_id}"].get("style")


def _rect_at(svg, g, nid):
    n = g.nodes[nid]
    return re.search(rf'<rect x="{n.cx - n.w / 2}" y="{n.cy - n.h / 2}" width="{n.w}" height="{n.h}"[^>]*>', svg).group(0)


def _poly_at(svg, g, nid):
    n = g.nodes[nid]
    return re.search(rf'<polygon points="{n.cx},{n.cy - n.h / 2} [^>]*>', svg).group(0)


def test_no_markers_matches_golden(demo_model):
    g = build_geometry(demo_model)
    assert render_svg(demo_model, geometry=g) == (FIX / "golden_demo.svg").read_text()
    assert render_drawio(demo_model, g) == (FIX / "golden_demo.drawio").read_text()
    assert "⚙" not in render_svg(demo_model, geometry=g)


def test_svg_removed_decision_and_task(demo_model):
    g = build_geometry(demo_model)
    svg = render_svg(demo_model, geometry=g, markers={"D1": "removed", "A3": "removed"})
    for el in (_poly_at(svg, g, "D1"), _rect_at(svg, g, "A3")):
        assert 'stroke="#9CA3AF"' in el and 'stroke-dasharray="5,3"' in el
    # an unmarked decision and task stay solid black
    for el in (_poly_at(svg, g, "D2"), _rect_at(svg, g, "A4")):
        assert "stroke-dasharray" not in el and "#9CA3AF" not in el


def test_svg_agent_double_border_and_glyphs(demo_model):
    g = build_geometry(demo_model)
    base = render_svg(demo_model, geometry=g)
    svg = render_svg(demo_model, geometry=g, markers=MARKS)
    n = g.nodes["A6"]
    inner = f'<rect x="{n.cx - n.w / 2 + 3}" y="{n.cy - n.h / 2 + 3}" width="{n.w - 6}" height="{n.h - 6}"'
    assert inner in svg and inner not in base
    # glyph drawn inside the marked boxes, at their top-left
    for nid, glyph in (("A2", "⚙"), ("A11", "✓")):
        m = g.nodes[nid]
        assert f'<text x="{m.cx - m.w / 2 + 7:.1f}" y="{m.cy - m.h / 2 + 13:.1f}"' in svg
        assert f">{glyph}</text>" in svg
    for word in ("Rules step", "Agent step", "Approval step", "Removed"):
        assert word in svg
    assert "Rules step" not in base


def test_drawio_markers_per_node(demo_model):
    g = build_geometry(demo_model)
    sub = next(n.id for n in g.nodes.values() if n.kind == "subprocess")
    dec = next(n.id for n in g.nodes.values() if n.kind == "decision")
    xml = render_drawio(demo_model, g, markers={"A6": "agent", "A2": "rules", "A11": "approval",
                                                 dec: "removed", "A3": "removed", sub: "agent"})
    assert "doubleRect" in _style(xml, "A6") and "doubleRect" in _style(xml, sub)
    for nid in (dec, "A3"):
        st = _style(xml, nid)
        assert "dashed=1" in st and "strokeColor=#9CA3AF" in st and "fontColor=#9CA3AF" in st
    assert _cells(xml)["n_A2"].get("value").startswith("⚙ ")
    assert _cells(xml)["n_A11"].get("value").startswith("✓ ")
    # untouched nodes keep the plain style
    plain = _style(xml, "A4")
    assert "doubleRect" not in plain and "dashed" not in plain
