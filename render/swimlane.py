"""
Deterministic ProcessModel -> swimlane, following the CGS Process Documentation Standards.

Two stages:
  build_geometry(model)  lays out lanes and shapes, places sticky notes, and routes every connector
  render_svg / drawio    draw that geometry (SVG for the app and PNG; draw.io XML for editing in Lucidchart)

Standards implemented
  - horizontal swimlanes, one per role (roles only, never names)
  - flow upper-left -> bottom-right; start event in the initiating role's lane
  - BPMN 2.0 shapes: start/end events, tasks, decisions (questions with labelled branches), predefined subprocess
  - right-angle connectors; lettered in-page connectors only for backward jumps across lanes or very long jumps
  - core diagram black & white; colour reserved for callouts (yellow sticky notes, italic text callouts)
  - CGS tool tags (e.g. "TL-08 · Insightly") above each task that uses a tool

Connector routing
  Every connector is routed on an 8px grid with A* search. Shapes and sticky notes are hard obstacles; text callouts,
  tool tags and the space just around shapes are soft obstacles. Running along another connector is heavily
  penalised (so two connectors never share a line unless they merge into the same target), crossings and bends are
  lightly penalised, and each branch of a decision leaves from its own side of the diamond.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from cgs_tools import ToolTag, tags_for
from schema import ProcessModel

ORANGE = "#FF6428"
INK = "#1F2328"
GREY = "#6B7280"
LANE_LINE = "#9CA3AF"
REMOVED = "#9CA3AF"          # grey for steps marked removed in the future state
STICKY = "#FFF3A3"
STICKY_EDGE = "#E3C94C"
TAG_EDGE = "#6B7280"
FILL = {"judgment": "#FFF0E8", "deterministic": "#EEF2F7", "unclear": "#FFFFFF"}
STROKE = {"judgment": ORANGE, "deterministic": "#5B6B7F", "unclear": INK}
PAIN = "#DC2626"
FONT = "Inter, Helvetica, Arial, sans-serif"

G = 8                    # routing grid; every centre and lane edge sits on it
COL_W = 208
NODE_W = 150
NODE_H = 56
ROW_H = 112
HEADER_W = 184
LEFT_PAD = 16
TOP_PAD = 64
LANE_PAD = 8
STICKY_W, STICKY_H = 150, 66
STICKY_BAND = 80         # lane band that holds sticky notes (top, or bottom for lower-row steps)
LOOP_BAND = 40           # extra room under a lane's rows when it has loops back to an earlier step
LOOP_MAX_COLS = 8        # same-lane loops longer than this use lettered connectors
TAG_H, TAG_FONT, TAG_CHAR = 13, 8, 4.6
DEC_W, DEC_H = NODE_W * 0.9, NODE_H * 1.3
EV_R = 15

E, S, W, N = 0, 1, 2, 3
DXY = {E: (1, 0), S: (0, 1), W: (-1, 0), N: (0, -1)}


# ================================================================== text helpers
def _wrap(text: str, max_chars: int = 20, max_lines: int = 3) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 1].rstrip() + "…"
    return lines


def _text(x, y, s, size=11, weight="400", anchor="middle", fill=INK, italic=False):
    st = ' font-style="italic"' if italic else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}"{st}>{escape(s)}</text>')


def _tag_w(tag: ToolTag) -> float:
    return len(tag.text) * TAG_CHAR + 10


def tag_rows(tags: List[ToolTag], max_w: float = COL_W - 24) -> List[List[ToolTag]]:
    rows, cur, w = [], [], 0.0
    for t in tags:
        tw = _tag_w(t)
        if cur and w + 4 + tw > max_w:
            rows.append(cur); cur, w = [], 0.0
        cur.append(t); w += (4 if w else 0) + tw
    if cur:
        rows.append(cur)
    return rows[:2]


# ================================================================== geometry
@dataclass
class NodeGeo:
    id: str
    kind: str                 # activity | subprocess | decision | start | end
    cx: float
    cy: float
    w: float
    h: float
    label: str
    role_id: str
    col: int
    row: int
    notes: str = ""
    step_type: str = "unclear"
    tags: List[ToolTag] = field(default_factory=list)


@dataclass
class EdgeGeo:
    from_id: str
    to_id: str
    label: str
    points: List[Tuple[float, float]]          # polyline, source boundary -> target boundary
    dashed: bool = False                        # loop back to an earlier step
    letter: Optional[str] = None                # lettered in-page connector instead of a drawn line
    exit_pt: Optional[Tuple[float, float]] = None
    entry_pt: Optional[Tuple[float, float]] = None


@dataclass
class StickyGeo:
    node_id: str
    x: float
    y: float
    text: str
    below: bool               # sticky sits under its step (leader goes up)


@dataclass
class Geometry:
    width: int
    height: int
    title: str
    lanes: List[Tuple[str, str, float, float]]  # role_id, name, y_top, height
    nodes: Dict[str, NodeGeo]
    edges: List[EdgeGeo]
    stickies: List[StickyGeo]
    connector_legend: List[str]


def _size(kind: str) -> Tuple[float, float]:
    if kind == "decision":
        return DEC_W, DEC_H
    if kind in ("start", "end"):
        return 2 * EV_R, 2 * EV_R
    return NODE_W, NODE_H


def _layout_slots(model: ProcessModel):
    """Column per distinct `order`; row inside each (lane, column) cell by barycentre of predecessors."""
    nodes = [(a.id, a.role_id, a.order) for a in model.activities] + [(d.id, d.role_id, d.order) for d in model.decisions]
    used_roles = [r for r in model.roles if any(n[1] == r.id for n in nodes)]
    lane_idx = {r.id: i for i, r in enumerate(used_roles)}
    orders = sorted({n[2] for n in nodes})
    col_of = {o: i for i, o in enumerate(orders)}
    cell: Dict[Tuple[str, int], List[str]] = defaultdict(list)
    for nid, rid, order in sorted(nodes, key=lambda n: (n[2], n[0])):
        cell[(rid, col_of[order])].append(nid)
    n_rows = {r.id: max([len(v) for (rid, _), v in cell.items() if rid == r.id] or [1]) for r in used_roles}

    preds: Dict[str, List[str]] = defaultdict(list)
    for e in model.edges:
        preds[e.to_id].append(e.from_id)
    end_ids = {a.id for a in model.activities if a.kind == "end"}
    role_of = {a.id: a.role_id for a in model.activities} | {d.id: d.role_id for d in model.decisions}
    slot: Dict[str, int] = {}
    col: Dict[str, int] = {}
    for c in range(len(orders)):
        for (rid, cc), ids in cell.items():
            if cc != c:
                continue

            def bary(nid):
                ys = [lane_idx[role_of[p]] * 100 + slot[p] for p in preds[nid] if p in slot]
                return (sum(ys) / len(ys)) if ys else float("inf")
            ids = sorted(ids, key=lambda n: (bary(n), n))
            used, s = set(), {}
            for nid in ids:          # end events sit on the row of their same-lane predecessor
                if nid in end_ids:
                    rows = [slot[p] for p in preds[nid] if p in slot and role_of.get(p) == rid]
                    if rows and min(max(rows), n_rows[rid] - 1) not in used:
                        s[nid] = min(max(rows), n_rows[rid] - 1); used.add(s[nid])
            free = (r for r in range(n_rows[rid] + len(ids)) if r not in used)
            for nid in ids:
                if nid not in s:
                    s[nid] = next(free)
            for nid in ids:
                slot[nid], col[nid] = s[nid], c
    return used_roles, slot, col, n_rows, len(orders)


_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"     # no I or O (read as 1 and 0)


def connector_letters():
    """A, B, … Z, then AA, AB, … AZ, BA, … with no limit (I and O skipped throughout)."""
    width = 1
    while True:
        idx = [0] * width
        while True:
            yield "".join(_LETTERS[i] for i in idx)
            j = width - 1
            while j >= 0 and idx[j] == len(_LETTERS) - 1:
                idx[j] = 0
                j -= 1
            if j < 0:
                break
            idx[j] += 1
        width += 1


def build_geometry(model: ProcessModel, show_stickies: bool = True, title: Optional[str] = None,
                   show_tools: bool = True) -> Geometry:
    used_roles, slot, col, n_rows, n_cols = _layout_slots(model)
    acts = {a.id: a for a in model.activities}
    decs = {d.id: d for d in model.decisions}
    role_of = {a.id: a.role_id for a in model.activities} | {d.id: d.role_id for d in model.decisions}

    # ---- sticky notes: open decisions / missing rules. Top band, or bottom band when a step sits above it
    sticky_text = {p.node_id: p.description for p in model.pain_points
                   if p.node_id and p.node_id in role_of and p.category in ("missing-rule", "ownership")} if show_stickies else {}
    occupied = {(role_of[n], col[n], slot[n]) for n in slot}
    sticky_below = {n: slot[n] > 0 and any((role_of[n], col[n], r) in occupied for r in range(slot[n]))
                    for n in sticky_text}

    # ---- loops: same-lane backward edges get a band under the lane's rows
    loop_lanes = {role_of[e.from_id] for e in model.edges
                  if e.from_id in col and e.to_id in col and col[e.to_id] <= col[e.from_id]
                  and role_of[e.from_id] == role_of[e.to_id]}

    lanes, lane_geo, y = [], {}, TOP_PAD
    for r in used_roles:
        top = STICKY_BAND if any(role_of[n] == r.id and not sticky_below[n] for n in sticky_text) else 0
        bottom = (STICKY_BAND if any(role_of[n] == r.id and sticky_below[n] for n in sticky_text) else 0) + \
                 (LOOP_BAND if r.id in loop_lanes else 0)
        h = n_rows[r.id] * ROW_H + 2 * LANE_PAD + top + bottom
        lanes.append((r.id, r.name, y, h))
        lane_geo[r.id] = (y, h, top)
        y += h
    width = LEFT_PAD + HEADER_W + n_cols * COL_W + 24
    height = y + 24

    nodes: Dict[str, NodeGeo] = {}
    for nid in slot:
        yt, h, top = lane_geo[role_of[nid]]
        cx = LEFT_PAD + HEADER_W + col[nid] * COL_W + COL_W / 2
        cy = yt + top + LANE_PAD + slot[nid] * ROW_H + ROW_H / 2
        if nid in decs:
            d = decs[nid]
            nodes[nid] = NodeGeo(nid, "decision", cx, cy, DEC_W, DEC_H, d.question, d.role_id, col[nid], slot[nid])
        else:
            a = acts[nid]
            w, hh = _size(a.kind)
            nodes[nid] = NodeGeo(nid, a.kind, cx, cy, w, hh, a.name, a.role_id, col[nid], slot[nid], a.notes,
                                 a.step_type, tags_for(a.tools) if show_tools and a.kind in ("activity", "subprocess") else [])

    stickies: List[StickyGeo] = []
    for nid, text in sticky_text.items():
        n = nodes[nid]
        yt, h, top = lane_geo[n.role_id]
        if sticky_below[nid]:
            sy = yt + h - STICKY_H - 8 - (LOOP_BAND if n.role_id in loop_lanes else 0)
        else:
            sy = yt + 6
        stickies.append(StickyGeo(nid, n.cx - STICKY_W / 2, sy, text, sticky_below[nid]))

    edges, legend = _route_all(model, nodes, stickies, lanes, width, height)
    return Geometry(int(width), int(height), title or f"{model.process.name} — current state",
                    [(rid, name, yt, h) for rid, name, yt, h in lanes], nodes, edges, stickies, legend)


# ================================================================== router
class _Grid:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.hard: set = set()
        self.soft: Dict[Tuple[int, int], float] = defaultdict(float)
        self.occ: Dict[Tuple[int, int], List[Tuple[int, int, str, str]]] = defaultdict(list)  # (edge, axis, src, tgt)

    @staticmethod
    def c(v: float) -> int:
        return int(round(v / G))

    def rect(self, x0, y0, x1, y1, cost: Optional[float] = None, strict: bool = True):
        """Mark grid points inside a rectangle (strictly inside when strict) as hard (cost None) or soft."""
        for ix in range(int(x0 // G), int(x1 // G) + 2):
            for iy in range(int(y0 // G), int(y1 // G) + 2):
                px, py = ix * G, iy * G
                inside = (x0 < px < x1 and y0 < py < y1) if strict else (x0 <= px <= x1 and y0 <= py <= y1)
                if inside:
                    if cost is None:
                        self.hard.add((ix, iy))
                    else:
                        self.soft[(ix, iy)] = max(self.soft[(ix, iy)], cost)

    def inside(self, ix, iy):
        return self.x0 <= ix <= self.x1 and self.y0 <= iy <= self.y1


def _ports(n: NodeGeo):
    """Grid points just outside each side: side -> (ix, iy, boundary point)."""
    c = _Grid.c
    hw, hh = n.w / 2, n.h / 2
    pad_x = G * ((int(hw + 4) // G) + 1)
    pad_y = G * ((int(hh + 4) // G) + 1)
    return {
        E: (c(n.cx + pad_x), c(n.cy), (n.cx + hw, n.cy)),
        W: (c(n.cx - pad_x), c(n.cy), (n.cx - hw, n.cy)),
        N: (c(n.cx), c(n.cy - pad_y), (n.cx, n.cy - hh)),
        S: (c(n.cx), c(n.cy + pad_y), (n.cx, n.cy + hh)),
    }


LEADER_DX = -36          # sticky leader meets the step left of centre, clear of the top/bottom connection points


def leader(n: NodeGeo, st: "StickyGeo") -> Tuple[float, float, float]:
    """x, y_from (sticky edge), y_to (step edge) of the dotted line joining a sticky note to its step."""
    x = n.cx + LEADER_DX
    if n.kind == "decision":
        edge = n.h / 2 * (1 - abs(LEADER_DX) / (n.w / 2))
    elif n.kind in ("start", "end"):
        edge = 0
    else:
        edge = n.h / 2
    if st.below:
        return x, st.y, n.cy + edge + (20 if n.notes else 2)
    top = n.cy - edge - (_tags_height(n) + 2 if n.tags else 2)
    return x, st.y + STICKY_H, top


def _tags_height(n: NodeGeo) -> float:
    return len(tag_rows(n.tags)) * (TAG_H + 2) + 3 if n.tags else 0


def _search(grid: _Grid, starts, goals, window, edge_idx: int, src: str, tgt: str):
    """A* over (cell, heading). starts: [(ix, iy, heading, penalty)]; goals: {(ix, iy, heading): penalty}."""
    x0, y0, x1, y1 = window
    BEND, CROSS, ALONG, MERGE = 4.0, 3.0, 40.0, 0.3
    gx = [g[0] for g in goals]; gy = [g[1] for g in goals]

    def h(ix, iy):
        return min(abs(ix - a) + abs(iy - b) for a, b in zip(gx, gy))

    heap, best, parent = [], {}, {}
    tick = 0
    for ix, iy, d, pen in starts:
        st = (ix, iy, d)
        best[st] = pen
        tick += 1
        heapq.heappush(heap, (pen + h(ix, iy), pen, tick, st))
    goal_state = None
    while heap:
        f, g, _, st = heapq.heappop(heap)
        if st == "GOAL":
            goal_state = True
            break
        if g > best.get(st, 1e18):
            continue
        ix, iy, d = st
        if (ix, iy, d) in goals:
            tot = g + goals[(ix, iy, d)]
            if tot < best.get("GOAL", 1e18):
                best["GOAL"] = tot
                parent["GOAL"] = st
                tick += 1
                heapq.heappush(heap, (tot, tot, tick, "GOAL"))
        for nd in (d, (d + 1) % 4, (d + 3) % 4):
            dx, dy = DXY[nd]
            nx, ny = ix + dx, iy + dy
            if not (x0 <= nx <= x1 and y0 <= ny <= y1) or (nx, ny) in grid.hard:
                continue
            cost = 1.0 + grid.soft.get((nx, ny), 0.0) + (BEND if nd != d else 0.0)
            axis = 0 if nd in (E, W) else 1
            for (oe, oaxis, osrc, otgt) in grid.occ.get((nx, ny), ()):
                if oe == edge_idx:
                    continue
                if oaxis == axis or oaxis == 2:
                    cost += MERGE if otgt == tgt else (2.0 if osrc == src else ALONG)
                else:
                    cost += CROSS
            ns = (nx, ny, nd)
            ng = g + cost
            if ng < best.get(ns, 1e18):
                best[ns] = ng
                parent[ns] = st
                tick += 1
                heapq.heappush(heap, (ng + h(nx, ny), ng, tick, ns))
    if not goal_state:
        return None
    path, st = [], parent["GOAL"]
    while st in parent:
        path.append(st)
        st = parent[st]
    path.append(st)
    path.reverse()
    return path


def _occupy(grid: _Grid, cells: List[Tuple[int, int, int]], edge_idx: int, src: str, tgt: str):
    for i, (ix, iy, d) in enumerate(cells):
        axis = 0 if d in (E, W) else 1
        if i + 1 < len(cells) and cells[i + 1][2] != d:
            axis = 2                                     # corner: blocks both directions
        grid.occ[(ix, iy)].append((edge_idx, axis, src, tgt))


def _simplify(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - p[0]) < 0.01) or (abs(a[1] - b[1]) < 0.01 and abs(b[1] - p[1]) < 0.01):
                out[-1] = p
                continue
        out.append(p)
    return out


def _route_all(model, nodes: Dict[str, NodeGeo], stickies: List[StickyGeo], lanes, width, height):
    grid = _Grid(_Grid.c(LEFT_PAD + HEADER_W) + 1, _Grid.c(TOP_PAD) + 1, _Grid.c(width - 24) - 1, _Grid.c(height - 24) - 1)
    for n in nodes.values():
        grid.rect(n.cx - n.w / 2 - 4, n.cy - n.h / 2 - 4, n.cx + n.w / 2 + 4, n.cy + n.h / 2 + 4)          # shape
        grid.rect(n.cx - n.w / 2 - 12, n.cy - n.h / 2 - 12, n.cx + n.w / 2 + 12, n.cy + n.h / 2 + 12, 1.5)  # clearance
        if n.notes:
            grid.rect(n.cx - 80, n.cy + n.h / 2 + 2, n.cx + 80, n.cy + n.h / 2 + 18, 20.0, strict=False)
        if n.tags:
            th = _tags_height(n)
            grid.rect(n.cx - COL_W / 2 + 10, n.cy - n.h / 2 - th - 2, n.cx + COL_W / 2 - 10, n.cy - n.h / 2, 6.0, strict=False)
        if n.kind in ("start", "end"):
            grid.rect(n.cx - 60, n.cy + EV_R + 2, n.cx + 60, n.cy + EV_R + 30, 6.0, strict=False)
    for _, _, yt, h in lanes:                               # keep connectors off the lane boundary lines
        for yb in (yt, yt + h):
            grid.rect(LEFT_PAD + HEADER_W, yb - 1, width, yb + 1, 15.0, strict=False)
    for s in stickies:
        grid.rect(s.x - 6, s.y - 6, s.x + STICKY_W + 6, s.y + STICKY_H + 6)
        lx, ya, yb = leader(nodes[s.node_id], s)          # keep connectors off the dotted leader
        grid.rect(lx - 2, min(ya, yb), lx + 2, max(ya, yb), 10.0, strict=False)

    cols = {k: n.col for k, n in nodes.items()}
    lane_of = {k: n.role_id for k, n in nodes.items()}
    ports = {k: _ports(n) for k, n in nodes.items()}
    used_start: Dict[str, set] = defaultdict(set)
    used_end: Dict[str, Dict[int, str]] = defaultdict(dict)      # node -> side -> source of first edge in

    def is_back(e):
        return cols[e.to_id] <= cols[e.from_id]

    lettered = []
    todo = []
    for i, e in enumerate(model.edges):
        if e.from_id not in nodes or e.to_id not in nodes:
            continue
        back = is_back(e)
        long_back = back and (lane_of[e.from_id] != lane_of[e.to_id] or cols[e.from_id] - cols[e.to_id] > LOOP_MAX_COLS
                              or (nodes[e.from_id].row != nodes[e.to_id].row and cols[e.from_id] - cols[e.to_id] >= 3))
        if long_back:
            lettered.append((i, e))
            continue
        span = abs(cols[e.to_id] - cols[e.from_id]) + abs(nodes[e.to_id].cy - nodes[e.from_id].cy) / ROW_H
        todo.append((1 if back else 0, span, i, e))
    todo.sort(key=lambda t: (t[0], t[1], t[2]))

    edges: List[EdgeGeo] = []
    for back, _, i, e in todo:
        src, tgt = nodes[e.from_id], nodes[e.to_id]
        sp, tp = ports[e.from_id], ports[e.to_id]
        starts = []
        for side, pen in ((E, 0), (S, 2), (N, 3)) if src.kind == "decision" else \
                         ((E, 0), (S, 8), (N, 12)) if src.kind != "start" else ((E, 0), (S, 6), (N, 6)):
            if src.kind == "decision" and side in used_start[e.from_id]:
                pen += 500
            if back and side == S:
                pen -= 1
            if side == N and src.tags:
                continue
            ix, iy, _ = sp[side]
            if (ix, iy) not in grid.hard:
                starts.append((ix, iy, side, pen))
        goals = {}
        for side, arrive, pen in ((W, E, 0), (N, S, 6), (S, N, 6)):
            if side == N and tgt.tags:
                continue                                  # tool tags sit on top of the task
            if side in used_end[e.to_id] and side != W:
                pen += 40                                 # top / bottom entries are for single connectors
            ix, iy, _ = tp[side]
            goals[(ix, iy, arrive)] = pen
        pts_x = [s[0] for s in starts] + [g[0] for g in goals]
        pts_y = [s[1] for s in starts] + [g[1] for g in goals]
        window = (max(grid.x0, min(pts_x) - 12), max(grid.y0, min(pts_y) - 24),
                  min(grid.x1, max(pts_x) + 12), min(grid.y1, max(pts_y) + 24))
        path = _search(grid, starts, goals, window, i, e.from_id, e.to_id)
        if path is None:
            path = _search(grid, starts, goals, (grid.x0, grid.y0, grid.x1, grid.y1), i, e.from_id, e.to_id)
        if path is None:
            lettered.append((i, e))
            continue
        _occupy(grid, path, i, e.from_id, e.to_id)
        s_side = path[0][2]
        used_start[e.from_id].add(s_side)
        last = path[-1]
        t_side = {E: W, S: N, N: S}[last[2]]
        used_end[e.to_id].setdefault(t_side, e.from_id)
        pts = [sp[s_side][2]] + [(ix * G, iy * G) for ix, iy, _ in path] + [tp[t_side][2]]
        edges.append(EdgeGeo(e.from_id, e.to_id, e.label, _simplify(pts), dashed=bool(back)))

    # ---- lettered in-page connectors (backward jumps across lanes, very long loops, or unroutable)
    legend = []
    letters = connector_letters()
    for i, e in lettered:
        letter = next(letters)
        src, tgt = nodes[e.from_id], nodes[e.to_id]
        side = next((s for s in (E, S, N) if s not in used_start[e.from_id]), E) if src.kind == "decision" else E
        used_start[e.from_id].add(side)
        bx, by = ports[e.from_id][side][2]
        dx, dy = DXY[side]
        exit_pt = (bx + dx * 34, by + dy * 34)
        ex, ey = ports[e.to_id][W][2]
        if W not in used_end[e.to_id]:
            entry_pt, tail = (ex - 34, ey), []
        else:                                            # left side already has a connector in: drop in from above-left
            entry_pt, tail = (ex - 26, ey - 34), [(ex - 26, ey)]
        used_end[e.to_id].setdefault(W, e.from_id)
        edges.append(EdgeGeo(e.from_id, e.to_id, e.label, [(bx, by), exit_pt, entry_pt] + tail + [(ex, ey)],
                             dashed=is_back(e), letter=letter, exit_pt=exit_pt, entry_pt=entry_pt))
        legend.append(f"{letter}: {e.label or 'continue'} → {tgt.label}")
    return edges, legend


# ================================================================== SVG
def _node_svg(n: NodeGeo, overlay, pain_nodes, coverage, marker: str | None = None) -> str:
    cx, cy = n.cx, n.cy
    out = []
    gone = marker == "removed"
    ink = REMOVED if gone else INK
    dash_attr = ' stroke-dasharray="5,3"' if gone else ""
    if n.kind == "decision":
        w, h = n.w, n.h
        out.append(f'<polygon points="{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}" '
                   f'fill="#FFFFFF" stroke="{ink}" stroke-width="1.5"{dash_attr}/>')
        lines = _wrap(n.label, 17, 3)
        y0 = cy - (len(lines) - 1) * 6.5 + 4
        for i, line in enumerate(lines):
            out.append(_text(cx, y0 + i * 13, line, 10.5, fill=ink))
        return "".join(out)

    if n.kind in ("start", "end"):
        sw = 1.5 if n.kind == "start" else 3.5
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{EV_R}" fill="#FFFFFF" stroke="{ink}" stroke-width="{sw}"{dash_attr}/>')
        label = n.label if n.kind == "start" else n.label.replace("End: ", "")
        for i, line in enumerate(_wrap(label, 22, 2)):
            out.append(_text(cx, cy + EV_R + 13 + i * 12, line, 9.5, fill=GREY))
        return "".join(out)

    fill = FILL[n.step_type] if overlay else "#FFFFFF"
    stroke = STROKE[n.step_type] if overlay else INK
    if gone:
        stroke = REMOVED
    out.append(f'<rect x="{cx-n.w/2}" y="{cy-n.h/2}" width="{n.w}" height="{n.h}" rx="7" '
               f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash_attr}/>')
    if marker == "agent":                                  # double border: a second outline just inside the first
        out.append(f'<rect x="{cx-n.w/2+3}" y="{cy-n.h/2+3}" width="{n.w-6}" height="{n.h-6}" rx="5" '
                   f'fill="none" stroke="{stroke}" stroke-width="1.2"/>')
    elif marker in ("rules", "approval"):
        out.append(_text(cx - n.w / 2 + 7, cy - n.h / 2 + 13, "⚙" if marker == "rules" else "✓", 11, "700", anchor="start"))
    if n.kind == "subprocess":
        bx, by = cx - 6, cy + n.h / 2 - 13
        out.append(f'<rect x="{bx}" y="{by}" width="12" height="12" fill="#fff" stroke="{stroke}" stroke-width="1.2"/>'
                   f'<line x1="{bx+2.5}" y1="{by+6}" x2="{bx+9.5}" y2="{by+6}" stroke="{stroke}" stroke-width="1.2"/>'
                   f'<line x1="{bx+6}" y1="{by+2.5}" x2="{bx+6}" y2="{by+9.5}" stroke="{stroke}" stroke-width="1.2"/>')
    lines = _wrap(n.label, 20, 2)
    y0 = cy - (len(lines) - 1) * 6.5 + 4 - (5 if n.kind == "subprocess" else 0)
    for i, line in enumerate(lines):
        out.append(_text(cx, y0 + i * 13, line, 11, fill=ink))
    if n.notes:
        out.append(_text(cx, cy + n.h / 2 + 12, _wrap(n.notes, 32, 1)[0], 8.5, fill=GREY, italic=True))

    # CGS tool tags, stacked upward from the top edge
    rows = tag_rows(n.tags)
    for ri, row in enumerate(rows):
        total = sum(_tag_w(t) for t in row) + 4 * (len(row) - 1)
        x = cx - total / 2
        ty = cy - n.h / 2 - 3 - (ri + 1) * TAG_H - ri * 2
        for t in row:
            tw = _tag_w(t)
            dash = '' if t.on_list else ' stroke-dasharray="3,2"'
            out.append(f'<rect x="{x:.1f}" y="{ty:.1f}" width="{tw:.1f}" height="{TAG_H}" rx="6.5" fill="#FFFFFF" '
                       f'stroke="{TAG_EDGE}" stroke-width="0.9"{dash}/>')
            out.append(_text(x + tw / 2, ty + TAG_H - 3.6, t.text, TAG_FONT, "500", fill="#374151"))
            x += tw + 4

    if overlay:
        if n.id in pain_nodes:
            bx, by = cx + n.w / 2 - 3, cy - n.h / 2 + 3
            out.append(f'<circle cx="{bx}" cy="{by}" r="8" fill="{PAIN}"/>' + _text(bx, by + 3.5, "!", 10, "700", fill="#fff"))
        k = coverage.get(n.id, 0)
        if k:
            out.append(_text(cx - n.w / 2 + 5, cy + n.h / 2 - 4, f"{k} src", 8, anchor="start", fill=GREY))
    return "".join(out)


def _label_pos(pts):
    """Branch label beside the first leg of the connector, just after it leaves the shape."""
    (x0, y0), (x1, y1) = pts[0], pts[1]
    if len(pts) > 2 and abs(x1 - x0) + abs(y1 - y0) < 14:      # skip the short stub out of the shape
        (x0, y0), (x1, y1) = pts[1], pts[2]
    if abs(y1 - y0) < 0.5:                                     # horizontal leg: above the line
        d = 1 if x1 > x0 else -1
        return x0 + d * 6, y0 - 5, "start" if d > 0 else "end"
    d = 1 if y1 > y0 else -1                                   # vertical leg: right of the line
    return x0 + 5, y0 + d * 14 + (4 if d > 0 else 0), "start"


def _edge_svg(e: EdgeGeo) -> str:
    dash = ' stroke-dasharray="5,4"' if e.dashed else ""
    out = []
    if e.letter:
        (bx, by), (ox, oy), (ix, iy) = e.points[:3]
        out.append(f'<line x1="{bx}" y1="{by}" x2="{ox}" y2="{oy}" stroke="{INK}" stroke-width="1.4"/>')
        out.append(f'<circle cx="{ox}" cy="{oy}" r="11" fill="#fff" stroke="{INK}" stroke-width="1.4"/>' + _text(ox, oy + 4, e.letter, 10, "700"))
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in e.points[2:])
        out.append(f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.4" marker-end="url(#arrow)"/>')
        out.append(f'<circle cx="{ix}" cy="{iy}" r="11" fill="#fff" stroke="{INK}" stroke-width="1.4"/>' + _text(ix, iy + 4, e.letter, 10, "700"))
        if e.label:
            w = len(e.label) * 5.6 + 8
            out.append(f'<rect x="{ox+13}" y="{oy-21}" width="{w}" height="13" fill="#fff"/>' + _text(ox + 16, oy - 11, e.label, 9, anchor="start"))
        return "".join(out)
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in e.points)
    out.append(f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.4"{dash} marker-end="url(#arrow)"/>')
    return "".join(out)


def _edge_label_svg(e: EdgeGeo) -> str:
    if not e.label or e.letter:
        return ""
    lx, ly, anchor = _label_pos(e.points)
    w = len(e.label) * 5.6 + 6
    bx = lx - 3 if anchor == "start" else lx - w + 3
    return f'<rect x="{bx:.1f}" y="{ly-9:.1f}" width="{w:.1f}" height="12" fill="#fff" opacity="0.92"/>' + \
        _text(lx, ly, e.label, 9, anchor=anchor)


def _sticky_svg(x, y, text) -> str:
    out = [f'<rect x="{x}" y="{y}" width="{STICKY_W}" height="{STICKY_H}" fill="{STICKY}" stroke="{STICKY_EDGE}" stroke-width="1"/>',
           f'<path d="M{x+STICKY_W-12},{y+STICKY_H} L{x+STICKY_W},{y+STICKY_H-12} L{x+STICKY_W},{y+STICKY_H} z" fill="{STICKY_EDGE}" opacity="0.6"/>']
    for i, line in enumerate(_wrap(text, 28, 4)):
        out.append(_text(x + 7, y + 14 + i * 12, line, 9, anchor="start"))
    return "".join(out)


def render_svg(model: ProcessModel, coverage: Dict[str, int] | None = None, title: str | None = None,
               overlay: bool = False, show_stickies: bool = True, show_tools: bool = True,
               geometry: Geometry | None = None, markers: Dict[str, str] | None = None) -> str:
    coverage = coverage or {}
    markers = markers or {}
    g = geometry or build_geometry(model, show_stickies=show_stickies, title=title, show_tools=show_tools)
    pain_nodes = {p.node_id for p in model.pain_points if p.node_id}
    width, height = g.width, g.height

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="{FONT}">',
         f'<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="userSpaceOnUse">'
         f'<path d="M0,0 L9,4.5 L0,9 z" fill="{INK}"/></marker></defs>',
         f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
         _text(LEFT_PAD, 28, g.title, 16, "700", anchor="start")]
    # legend
    lx = LEFT_PAD
    items = [("task", "rect"), ("decision", "diamond"), ("subprocess", "sub"), ("start / end", "circ"),
             ("loop back", "dash"), ("connector", "conn"), ("open item", "sticky")]
    if any(n.tags for n in g.nodes.values()):
        items += [("CGS tool", "tag")]
        if any(not t.on_list for n in g.nodes.values() for t in n.tags):
            items += [("not on CGS tool list", "tag_off")]
    if markers:
        items += [("Rules step", "m_rules"), ("Agent step", "m_agent"), ("Approval step", "m_approval")]
        if "removed" in markers.values():
            items += [("Removed", "m_removed")]
    if overlay:
        items += [("judgment", "ov_j"), ("rule-based", "ov_d"), ("pain point", "pain")]
    for name, kind in items:
        y = 46
        if kind == "rect": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{INK}"/>')
        elif kind == "diamond": s.append(f'<polygon points="{lx+8},{y-7} {lx+16},{y} {lx+8},{y+7} {lx},{y}" fill="#fff" stroke="{INK}"/>')
        elif kind == "m_rules": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{INK}"/>' + _text(lx+2, y+4, "⚙", 9, "700", anchor="start"))
        elif kind == "m_approval": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{INK}"/>' + _text(lx+2, y+4, "✓", 9, "700", anchor="start"))
        elif kind == "m_agent": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{INK}"/><rect x="{lx+2}" y="{y-4}" width="12" height="8" rx="1" fill="none" stroke="{INK}"/>')
        elif kind == "m_removed": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{REMOVED}" stroke-dasharray="3,2"/>')
        elif kind == "sub": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="#fff" stroke="{INK}"/>' + _text(lx+8, y+3, "+", 9, "700"))
        elif kind == "circ": s.append(f'<circle cx="{lx+8}" cy="{y}" r="6" fill="#fff" stroke="{INK}" stroke-width="2"/>')
        elif kind == "dash": s.append(f'<line x1="{lx}" y1="{y}" x2="{lx+16}" y2="{y}" stroke="{INK}" stroke-width="1.4" stroke-dasharray="4,3"/>')
        elif kind == "conn": s.append(f'<circle cx="{lx+8}" cy="{y}" r="7" fill="#fff" stroke="{INK}"/>' + _text(lx+8, y+3, "A", 8, "700"))
        elif kind == "sticky": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" fill="{STICKY}" stroke="{STICKY_EDGE}"/>')
        elif kind in ("tag", "tag_off"):
            dash = ' stroke-dasharray="3,2"' if kind == "tag_off" else ""
            s.append(f'<rect x="{lx}" y="{y-6}" width="34" height="12" rx="6" fill="#fff" stroke="{TAG_EDGE}"{dash}/>' + _text(lx+17, y+3, "TL-08" if kind == "tag" else "Tool", 7.5, "500"))
            lx += 18
        elif kind == "ov_j": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="{FILL["judgment"]}" stroke="{STROKE["judgment"]}"/>')
        elif kind == "ov_d": s.append(f'<rect x="{lx}" y="{y-6}" width="16" height="12" rx="2" fill="{FILL["deterministic"]}" stroke="{STROKE["deterministic"]}"/>')
        elif kind == "pain": s.append(f'<circle cx="{lx+8}" cy="{y}" r="6" fill="{PAIN}"/>')
        s.append(_text(lx + 21, y + 4, name, 10, anchor="start"))
        lx += 21 + len(name) * 6 + 16

    for rid, name, yt, h in g.lanes:
        s.append(f'<rect x="{LEFT_PAD}" y="{yt}" width="{width-LEFT_PAD-24}" height="{h}" fill="#FFFFFF" stroke="{LANE_LINE}"/>')
        s.append(f'<rect x="{LEFT_PAD}" y="{yt}" width="{HEADER_W}" height="{h}" fill="#F3F4F6" stroke="{LANE_LINE}"/>')
        s.append(f'<rect x="{LEFT_PAD}" y="{yt}" width="4" height="{h}" fill="{ORANGE}"/>')
        name_lines = _wrap(name, 25, 3)
        y0 = yt + h / 2 - (len(name_lines) - 1) * 7 + 4
        for j, line in enumerate(name_lines):
            s.append(_text(LEFT_PAD + 14, y0 + j * 14, line, 11.5, "600", anchor="start"))

    # sticky leaders first (under everything), then connectors, shapes, labels, notes
    for st_ in g.stickies:
        lx, y1, y2 = leader(g.nodes[st_.node_id], st_)
        s.append(f'<line x1="{lx}" y1="{y1}" x2="{lx}" y2="{y2}" stroke="{STICKY_EDGE}" stroke-width="1.2" stroke-dasharray="2,2"/>')
    for e in g.edges:
        s.append(_edge_svg(e))
    for n in g.nodes.values():
        s.append(_node_svg(n, overlay, pain_nodes, coverage, markers.get(n.id)))
    for e in g.edges:
        s.append(_edge_label_svg(e))
    for st_ in g.stickies:
        s.append(_sticky_svg(st_.x, st_.y, st_.text))
    if g.connector_legend:
        s.append(_text(LEFT_PAD, height - 8, "Connectors — " + "   ·   ".join(g.connector_legend), 9, anchor="start", fill=GREY))
    s.append("</svg>")
    return "".join(s)


def render_png(svg: str) -> bytes:
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=2800)
