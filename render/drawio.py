"""
Swimlane -> draw.io (diagrams.net) file, for editing in draw.io or Lucidchart (File → Import → draw.io).

Uses the same geometry as the SVG, so the file opens looking like the diagram in the app. Everything stays editable:
  - each role is a swimlane container; steps are inside their lane, so dragging a step into another lane moves it there
  - steps, decisions and events are real shapes; connectors are attached to them and follow when a shape moves
  - CGS tool tags and the italic text callout are attached to their step and move with it
  - sticky notes are attached to their step with a dotted line
Lettered in-page connectors are drawn as ordinary dashed connectors here, since an editable diagram does not need them.
"""
from __future__ import annotations

from typing import List, Tuple
from xml.sax.saxutils import escape, quoteattr

from render.swimlane import (EV_R, HEADER_W, LEFT_PAD, STICKY_EDGE, STICKY_H, STICKY_W, STICKY, TAG_H,
                             Geometry, _tag_w, tag_rows)
from schema import ProcessModel

HEADER = 40      # draw.io lane header (vertical label), aligned so lane content starts where it does in the SVG


def _side(a: Tuple[float, float], b: Tuple[float, float]) -> Tuple[float, float]:
    """Connection point (draw.io exitX/exitY style) for a connector leaving a shape from a toward b."""
    if abs(a[1] - b[1]) < 0.5:
        return (1.0, 0.5) if b[0] > a[0] else (0.0, 0.5)
    return (0.5, 1.0) if b[1] > a[1] else (0.5, 0.0)


def render_drawio(model: ProcessModel, g: Geometry, markers: dict[str, str] | None = None) -> str:
    markers = markers or {}
    cells: List[str] = []

    def vertex(cid, value, style, x, y, w, h, parent="1"):
        cells.append(f'<mxCell id="{cid}" value={quoteattr(value)} style="{style}" vertex="1" parent="{parent}">'
                     f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" as="geometry"/></mxCell>')

    vertex("title", f"<b>{escape(g.title)}</b>", "text;html=1;align=left;verticalAlign=middle;fontSize=16;strokeColor=none;fillColor=none;",
           LEFT_PAD, 12, 900, 28)

    lane_x = LEFT_PAD + HEADER_W - HEADER
    lane_w = g.width - 24 - lane_x
    lane_pos = {}
    for rid, name, yt, h in g.lanes:
        vertex(f"lane_{rid}", escape(name),
               f"swimlane;horizontal=0;startSize={HEADER};html=1;whiteSpace=wrap;fontStyle=1;fontSize=12;"
               "fillColor=#F3F4F6;swimlaneFillColor=#FFFFFF;strokeColor=#9CA3AF;collapsible=0;",
               lane_x, yt, lane_w, h)
        lane_pos[rid] = (lane_x, yt)

    for n in g.nodes.values():
        lx, ly = lane_pos[n.role_id]
        x, y = n.cx - n.w / 2 - lx, n.cy - n.h / 2 - ly
        nid = f"n_{n.id}"
        label = escape(n.label)
        if n.kind == "decision":
            style = "rhombus;whiteSpace=wrap;html=1;fontSize=10;strokeWidth=1.5;"
        elif n.kind in ("start", "end"):
            style = ("ellipse;html=1;whiteSpace=wrap;verticalLabelPosition=bottom;verticalAlign=top;fontSize=9;fontColor=#6B7280;"
                     f"strokeWidth={'1.5' if n.kind == 'start' else '3.5'};aspect=fixed;")
            label = escape(n.label.replace("End: ", "")) if n.kind == "end" else label
        elif n.kind == "subprocess":
            style = "rounded=1;arcSize=12;whiteSpace=wrap;html=1;fontSize=11;strokeWidth=1.5;"
            label += "<br>⊞"
        else:
            style = "rounded=1;arcSize=12;whiteSpace=wrap;html=1;fontSize=11;strokeWidth=1.5;"
            m = markers.get(n.id)
            if m == "agent":
                style = "shape=doubleRect;rounded=1;arcSize=12;whiteSpace=wrap;html=1;fontSize=11;strokeWidth=1.5;"
            elif m == "rules":
                label = "⚙ " + label
            elif m == "approval":
                label = "✓ " + label
            elif m == "removed":
                style += "dashed=1;strokeColor=#9CA3AF;fontColor=#9CA3AF;"
        vertex(nid, label, style, x, y, n.w, n.h, parent=f"lane_{n.role_id}")

        # tool tags and text callout as children of the step (relative to it), so they move with it
        for ri, row in enumerate(tag_rows(n.tags)):
            total = sum(_tag_w(t) for t in row) + 4 * (len(row) - 1)
            tx = n.w / 2 - total / 2
            ty = -3 - (ri + 1) * TAG_H - ri * 2
            for k, t in enumerate(row):
                tw = _tag_w(t)
                dash = "" if t.on_list else "dashed=1;"
                tip = t.text if t.on_list else f"{t.text} (not on CGS tool list)"
                cells.append(f'<mxCell id="{nid}_tag{ri}{k}" value={quoteattr(escape(t.text))} '
                             f'style="rounded=1;arcSize=50;html=1;fontSize=8;fontColor=#374151;strokeColor=#6B7280;{dash}'
                             f'fillColor=#FFFFFF;spacing=0;tooltip={escape(tip)};movable=0;" vertex="1" parent="{nid}">'
                             f'<mxGeometry x="{tx:.1f}" y="{ty:.1f}" width="{tw:.1f}" height="{TAG_H}" as="geometry"/></mxCell>')
                tx += tw + 4
        if n.notes:
            cells.append(f'<mxCell id="{nid}_note" value={quoteattr(escape(n.notes))} '
                         'style="text;html=1;whiteSpace=wrap;fontSize=8;fontStyle=2;fontColor=#6B7280;align=center;'
                         'verticalAlign=top;movable=0;" vertex="1" parent="' + nid + '">'
                         f'<mxGeometry x="-15" y="{n.h + 2}" width="{n.w + 30}" height="24" as="geometry"/></mxCell>')

    for i, st in enumerate(g.stickies):
        vertex(f"sticky_{i}", escape(st.text),
               f"shape=note;size=12;whiteSpace=wrap;html=1;fontSize=9;align=left;verticalAlign=top;spacing=6;"
               f"fillColor={STICKY};strokeColor={STICKY_EDGE};",
               st.x, st.y, STICKY_W, STICKY_H)
        cells.append(f'<mxCell id="sticky_{i}_link" style="endArrow=none;dashed=1;dashPattern=2 2;strokeColor={STICKY_EDGE};html=1;" '
                     f'edge="1" parent="1" source="sticky_{i}" target="n_{st.node_id}">'
                     '<mxGeometry relative="1" as="geometry"/></mxCell>')

    for i, e in enumerate(g.edges):
        style = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeWidth=1.4;fontSize=9;labelBackgroundColor=#FFFFFF;"
        if e.dashed:
            style += "dashed=1;"
        pts = e.points
        waypoints = ""
        if not e.letter and len(pts) >= 2:
            ex, ey = _side(pts[0], pts[1])
            nx_, ny_ = _side(pts[-1], pts[-2])
            style += f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;entryX={nx_};entryY={ny_};entryDx=0;entryDy=0;"
            inner = pts[1:-1]
            if inner:
                waypoints = '<Array as="points">' + "".join(f'<mxPoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in inner) + "</Array>"
        cells.append(f'<mxCell id="e_{i}" value={quoteattr(escape(e.label))} style="{style}" edge="1" parent="1" '
                     f'source="n_{e.from_id}" target="n_{e.to_id}"><mxGeometry relative="1" as="geometry">{waypoints}'
                     '</mxGeometry></mxCell>')

    body = "".join(cells)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mxfile host="CGS Stage 1" type="device">'
            f'<diagram id="current-state" name={quoteattr(model.process.name[:60] or "Current state")}>'
            f'<mxGraphModel dx="{g.width}" dy="{g.height}" grid="1" gridSize="8" guides="1" tooltips="1" connect="1" arrows="1" '
            f'fold="1" page="1" pageScale="1" pageWidth="{g.width}" pageHeight="{g.height}" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram></mxfile>\n')


def drawio_url(xml_text: str) -> str:
    """Link that opens the diagram straight in draw.io in the browser (no account needed); nothing is uploaded
    to a server: the diagram travels compressed in the part of the link after '#'."""
    import base64
    import urllib.parse
    import zlib
    data = urllib.parse.quote(xml_text, safe="-_.!~*'()")                 # = encodeURIComponent
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = comp.compress(data.encode("utf-8")) + comp.flush()
    return "https://app.diagrams.net/?splash=0&ui=kennedy#R" + urllib.parse.quote(base64.b64encode(raw).decode("ascii"), safe="")
