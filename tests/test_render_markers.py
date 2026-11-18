from render.drawio import render_drawio
from render.swimlane import build_geometry, render_svg


def test_markers_render(demo_model):
    g = build_geometry(demo_model)
    svg = render_svg(demo_model, geometry=g, markers={"A2": "rules", "A6": "agent", "A11": "approval", "D1": "removed"})
    assert "⚙" in svg and "✓" in svg and "Agent" in svg   # legend
    xml = render_drawio(demo_model, g, markers={"A6": "agent", "D1": "removed"})
    assert "dashed=1" in xml and ("doubleRect" in xml or "double=1" in xml)


def test_no_markers_unchanged(demo_model):
    g = build_geometry(demo_model)
    assert render_svg(demo_model, geometry=g) == render_svg(demo_model, geometry=g, markers=None)
    assert "⚙" not in render_svg(demo_model, geometry=g)
