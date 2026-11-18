"""
Read an edited draw.io file back into the ProcessModel.

The file is one exported by render/drawio.py and then edited in draw.io (app.diagrams.net or desktop). Shapes keep
the ids we gave them (n_A1, lane_R2, ...), so each edit can be traced back to the model:

  - step dragged into another lane          -> role_id changes
  - step dragged left/right                 -> order changes (columns are re-derived from horizontal position)
  - step, decision or lane renamed          -> name / question / role name changes
  - lanes reordered top-to-bottom           -> role order changes
  - connector added, removed or re-pointed  -> edges (and decision branches) change
  - connector label edited                  -> branch label changes
  - new task / decision / lane drawn        -> new activity / decision / role (new ids)
  - shape deleted                           -> removed from the model (pain points on it are kept, unlinked)
  - tool tag deleted or edited, note edited -> tools / notes change

Sticky notes, colours and connector waypoints are ignored: the app redraws the layout and routing itself.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from html import unescape
from typing import Dict, List, Optional, Tuple

from cgs_tools import resolve, tags_for
from render.swimlane import COL_W, HEADER_W, LEFT_PAD
from schema import Activity, Branch, Decision, Edge, Handoff, ProcessModel, Role

COLUMN_GAP = 90          # shapes whose centres are closer than this horizontally share a column


@dataclass
class ImportResult:
    model: ProcessModel
    changes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ file reading
def _diagram_root(xml_text: str) -> ET.Element:
    root = ET.fromstring(xml_text)
    if root.tag == "mxGraphModel":
        return root
    diagram = root.find("diagram") if root.tag == "mxfile" else None
    if diagram is None:
        raise ValueError("Not a draw.io file (no <diagram> found).")
    model = diagram.find("mxGraphModel")
    if model is not None:
        return model
    # compressed diagram: base64 -> raw deflate -> URL-encoded XML
    data = (diagram.text or "").strip()
    if not data:
        raise ValueError("The draw.io file has an empty diagram.")
    raw = zlib.decompress(base64.b64decode(data), -15)
    return ET.fromstring(urllib.parse.unquote(raw.decode("utf-8")))


def _plain(value: Optional[str]) -> str:
    """draw.io labels are HTML: strip tags, keep line breaks as spaces."""
    v = value or ""
    v = re.sub(r"<br\s*/?>|</div>|</p>", " ", v, flags=re.I)
    v = re.sub(r"<[^>]+>", "", v)
    v = unescape(v).replace("\xa0", " ")
    return re.sub(r"\s+", " ", v).strip()


def _style(cell: ET.Element) -> str:
    return cell.get("style") or ""


# ------------------------------------------------------------------ main
def import_drawio(xml_text: str, model: ProcessModel) -> ImportResult:
    gm = _diagram_root(xml_text)
    cells: Dict[str, ET.Element] = {c.get("id"): c for c in gm.iter("mxCell")}
    geo: Dict[str, Tuple[float, float, float, float]] = {}
    for cid, c in cells.items():
        g = c.find("mxGeometry")
        if g is not None and c.get("vertex") == "1":
            geo[cid] = (float(g.get("x", 0)), float(g.get("y", 0)), float(g.get("width", 0)), float(g.get("height", 0)))

    def absolute(cid: str) -> Tuple[float, float]:
        x, y = geo[cid][0], geo[cid][1]
        p = cells[cid].get("parent")
        while p in geo:
            x += geo[p][0]; y += geo[p][1]
            p = cells[p].get("parent")
        return x, y

    def centre(cid: str) -> Tuple[float, float]:
        x, y = absolute(cid)
        return x + geo[cid][2] / 2, y + geo[cid][3] / 2

    m = model.model_copy(deep=True)
    out = ImportResult(m)
    ch = out.changes
    acts = {a.id: a for a in m.activities}
    decs = {d.id: d for d in m.decisions}
    roles = {r.id: r for r in m.roles}
    name_of = lambda nid: (acts[nid].name if nid in acts else decs[nid].question if nid in decs else nid)  # noqa: E731

    # ---- lanes
    lane_cells = {cid: c for cid, c in cells.items() if c.get("vertex") == "1" and _style(c).startswith("swimlane")}
    lane_role: Dict[str, str] = {}
    next_r = max([int(r.id[1:]) for r in m.roles if re.fullmatch(r"R\d+", r.id)] or [0]) + 1
    for cid, c in lane_cells.items():
        label = _plain(c.get("value"))
        rid = cid[5:] if cid.startswith("lane_") and cid[5:] in roles else None
        if rid is None:
            rid = f"R{next_r}"; next_r += 1
            role = Role(id=rid, name=label or f"New role {rid}")
            m.roles.append(role); roles[rid] = role
            ch.append(f"Added lane **{role.name}**")
        elif label and label != roles[rid].name:
            ch.append(f"Renamed lane **{roles[rid].name}** → **{label}**")
            roles[rid].name = label
        lane_role[cid] = rid
    # lane order top-to-bottom
    lane_y = {lane_role[cid]: absolute(cid)[1] for cid in lane_cells}
    old_order = [r.id for r in m.roles if r.id in lane_y]
    new_order = sorted(lane_y, key=lambda r: lane_y[r])
    if old_order != new_order:
        ch.append("Reordered lanes: " + " → ".join(roles[r].name for r in new_order))
    m.roles = sorted(m.roles, key=lambda r: lane_y.get(r.id, 1e9))

    def lane_at(cid: str) -> Optional[str]:
        """Role of the lane a shape sits in: its parent lane, or the lane under its centre."""
        p = cells[cid].get("parent")
        if p in lane_role:
            return lane_role[p]
        cx, cy = centre(cid)
        for lid in lane_cells:
            x, y = absolute(lid); w, h = geo[lid][2], geo[lid][3]
            if x <= cx <= x + w and y <= cy <= y + h:
                return lane_role[lid]
        return None

    # ---- shapes
    shape_cells = {}
    for cid, c in cells.items():
        if c.get("vertex") != "1" or cid in lane_cells or cid == "title":
            continue
        st = _style(c)
        if cid.startswith("sticky_") or "_tag" in cid or cid.endswith("_note") or st.startswith("text") or "shape=note" in st:
            continue
        if cells.get(c.get("parent") or "", None) is not None and cells[c.get("parent")].get("vertex") == "1" \
                and c.get("parent") not in lane_cells:
            continue                                   # child decoration of another shape
        shape_cells[cid] = c

    seen: set = set()
    new_ids: Dict[str, str] = {}
    next_a = max([int(a.id[1:]) for a in m.activities if re.fullmatch(r"[AES]\d+", a.id)] or [0]) + 1
    next_d = max([int(d.id[1:]) for d in m.decisions if re.fullmatch(r"D\d+", d.id)] or [0]) + 1
    edges_out = {c.get("source") for c in cells.values() if c.get("edge") == "1"}
    for cid, c in shape_cells.items():
        st, label = _style(c), _plain(c.get("value"))
        nid = cid[2:] if cid.startswith("n_") and (cid[2:] in acts or cid[2:] in decs) else None
        rid = lane_at(cid)
        if nid is None:                                 # a shape the reviewer drew
            if not rid:
                out.warnings.append(f"Ignored a new shape outside every lane: “{label or cid}”.")
                continue
            if "rhombus" in st:
                nid = f"D{next_d}"; next_d += 1
                decs[nid] = Decision(id=nid, question=label or "New decision?", role_id=rid, order=0)
                m.decisions.append(decs[nid])
                ch.append(f"Added decision **{decs[nid].question}** in {roles[rid].name}")
            else:
                kind = ("end" if cid not in edges_out else "start") if "ellipse" in st else \
                       ("subprocess" if "⊞" in label or "shape=process" in st else "activity")
                nid = f"A{next_a}"; next_a += 1
                acts[nid] = Activity(id=nid, name=label.replace("⊞", "").strip() or "New step", role_id=rid,
                                     kind=kind, order=0)
                m.activities.append(acts[nid])
                ch.append(f"Added {'step' if kind in ('activity', 'subprocess') else kind + ' event'} "
                          f"**{acts[nid].name}** in {roles[rid].name}")
            new_ids[cid] = nid
        else:
            new_ids[cid] = nid
            node = acts.get(nid) or decs.get(nid)
            if rid and rid != node.role_id:
                ch.append(f"Moved **{name_of(nid)}** from {roles[node.role_id].name if node.role_id in roles else node.role_id} "
                          f"to **{roles[rid].name}**")
                node.role_id = rid
            if nid in decs:
                if label and label != decs[nid].question:
                    ch.append(f"Renamed decision **{decs[nid].question}** → **{label}**")
                    decs[nid].question = label
            else:
                a = acts[nid]
                clean = label.replace("⊞", "").strip()
                shown = a.name.replace("End: ", "") if a.kind == "end" else a.name
                if clean and clean != shown:
                    ch.append(f"Renamed **{a.name}** → **{clean}**")
                    a.name = clean
                _read_decorations(cells, cid, a, ch)
        seen.add(nid)

    # ---- deleted shapes
    for nid in [n for n in list(acts) + list(decs) if n not in seen]:
        rid = (acts.get(nid) or decs.get(nid)).role_id
        ch.append(f"Removed **{name_of(nid)}** ({roles[rid].name if rid in roles else rid})")
        for p in m.pain_points:
            if p.node_id == nid:
                p.node_id = None
                out.warnings.append(f"Pain point {p.id} was on a removed step; it is kept but no longer linked to a step.")
        acts.pop(nid, None); decs.pop(nid, None)
    m.activities = [a for a in m.activities if a.id in acts]
    m.decisions = [d for d in m.decisions if d.id in decs]

    # ---- order from horizontal position (columns re-derived; untouched shapes keep their relative order)
    xs = sorted((centre(cid)[0], nid) for cid, nid in new_ids.items() if nid in acts or nid in decs)
    col, anchor, order_of = -1, None, {}
    for x, nid in xs:
        if anchor is None or x - anchor > COLUMN_GAP:
            col += 1; anchor = x
        order_of[nid] = (col + 1) * 10
    for nid, o in order_of.items():
        (acts.get(nid) or decs.get(nid)).order = o
    # report only the shapes the reviewer actually dragged sideways (compared with where the app drew them)
    orig_orders = sorted({n.order for n in list(model.activities) + list(model.decisions)})
    orig_x = {n.id: LEFT_PAD + HEADER_W + orig_orders.index(n.order) * COL_W + COL_W / 2
              for n in list(model.activities) + list(model.decisions)}
    moved = []
    for cid, nid in new_ids.items():
        if nid in orig_x and nid in order_of:
            dx = centre(cid)[0] - orig_x[nid]
            if abs(dx) > COL_W / 2:
                moved.append((nid, dx))
    if len(moved) <= 6:
        for nid, dx in moved:
            ch.append(f"Moved **{name_of(nid)}** {'later' if dx > 0 else 'earlier'} in the flow")
    else:
        ch.append(f"Moved {len(moved)} steps along the flow")

    # ---- connectors
    old_edges = {(e.from_id, e.to_id): e.label for e in model.edges}
    new_edges: Dict[Tuple[str, str], str] = {}
    for cid, c in cells.items():
        if c.get("edge") != "1" or (c.get("style") or "").find("endArrow=none") >= 0:
            continue
        def owner(ref):                                 # a connector dropped on a tag or note belongs to its step
            ref = ref or ""
            while ref and ref not in new_ids and ref in cells and cells[ref].get("parent") in cells \
                    and cells[ref].get("parent") not in lane_cells and cells[ref].get("parent") not in ("0", "1"):
                ref = cells[ref].get("parent")
            return new_ids.get(ref)
        s, t = owner(c.get("source")), owner(c.get("target"))
        if not s or not t:
            if c.get("source") in new_ids or c.get("target") in new_ids:
                out.warnings.append("Ignored a connector that is not attached at both ends "
                                    f"({_plain(c.get('value')) or 'no label'}). Attach both ends to shapes in draw.io.")
            continue
        new_edges[(s, t)] = _plain(c.get("value"))
    for k, lab in new_edges.items():
        if k not in old_edges:
            ch.append(f"Added connector **{name_of(k[0])}** → **{name_of(k[1])}**" + (f" ({lab})" if lab else ""))
        elif old_edges[k] != lab:
            ch.append(f"Relabelled connector **{name_of(k[0])}** → **{name_of(k[1])}**: “{old_edges[k]}” → “{lab}”")
    for k in old_edges:
        if k not in new_edges and k[0] in seen and k[1] in seen:
            ch.append(f"Removed connector **{name_of(k[0])}** → **{name_of(k[1])}**")
    m.edges = [Edge(from_id=s, to_id=t, label=lab) for (s, t), lab in new_edges.items()]
    for d in m.decisions:
        d.branches = [Branch(label=e.label, next_id=e.to_id) for e in m.edges if e.from_id == d.id]

    # ---- handoffs follow the connectors: keep descriptions where the handoff still exists
    role_of = {a.id: a.role_id for a in m.activities} | {d.id: d.role_id for d in m.decisions}
    old_h = {(h.from_role_id, h.to_role_id, h.at_node_id): h.description for h in model.handoffs}
    hs = {}
    for e in m.edges:
        r1, r2 = role_of.get(e.from_id), role_of.get(e.to_id)
        if r1 and r2 and r1 != r2:
            key = (r1, r2, e.to_id)
            hs[key] = Handoff(from_role_id=r1, to_role_id=r2, at_node_id=e.to_id, description=old_h.get(key, ""))
    m.handoffs = list(hs.values())
    m.dependencies = [d for d in m.dependencies if d.node_id in role_of]

    # a lane the reviewer deleted removes its role; roles that had no lane in the diagram (no steps) are kept
    lanes_present = set(lane_role.values())
    had_lane = {n.role_id for n in list(model.activities) + list(model.decisions)}
    gone = [r for r in m.roles if r.id in had_lane and r.id not in lanes_present and r.id not in set(role_of.values())]
    for r in gone:
        ch.append(f"Removed lane **{r.name}**")
    m.roles = [r for r in m.roles if r not in gone]

    problems = m.validate_graph()
    out.warnings += problems
    for a in m.activities:
        if a.kind not in ("start", "end") and not any(e.to_id == a.id for e in m.edges):
            out.warnings.append(f"“{a.name}” has no incoming connector.")
        if a.kind != "end" and not any(e.from_id == a.id for e in m.edges):
            out.warnings.append(f"“{a.name}” has no outgoing connector.")
    if not ch:
        ch.append("No changes found — the file matches the current model.")
    return out


def _read_decorations(cells: Dict[str, ET.Element], cid: str, a: Activity, ch: List[str]) -> None:
    """Tool tags and text callout attached to a step."""
    tag_texts = [_plain(c.get("value")) for k, c in cells.items() if k.startswith(f"{cid}_tag") and c.get("parent") == cid]
    current = [t.text for t in tags_for(a.tools)]
    had_tags = bool(current)
    if (tag_texts or had_tags) and sorted(tag_texts) != sorted(current):
        # keep the extracted name for tags that are unchanged; take the label for new or edited ones
        by_text = {resolve(t).text: t for t in a.tools}
        new_tools = []
        for txt in tag_texts:
            if txt in by_text:
                new_tools.append(by_text[txt])
            else:
                new_tools.append(txt.split("·", 1)[1].strip() if "·" in txt else txt)
        removed = [t for t in current if t not in tag_texts]
        added = [t for t in tag_texts if t not in current]
        if removed:
            ch.append(f"Removed tool tag{'s' if len(removed) > 1 else ''} {', '.join(removed)} from **{a.name}**")
        if added:
            ch.append(f"Added tool tag{'s' if len(added) > 1 else ''} {', '.join(added)} to **{a.name}**")
        a.tools = new_tools
    note = cells.get(f"{cid}_note")
    if note is not None:
        txt = _plain(note.get("value"))
        if txt != a.notes:
            ch.append(f"Edited the note on **{a.name}**")
            a.notes = txt
    elif a.notes:
        ch.append(f"Removed the note on **{a.name}**")
        a.notes = ""
