"""Graph facts derived from a ProcessModel, used by the framework rules."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from schema import ProcessModel


@dataclass
class GraphFacts:
    steps: list[str]
    succ: dict[str, set[str]]
    pred: dict[str, set[str]]
    actor: dict[str, str]
    same_actor_neighbours: dict[str, set[str]]
    start_ids: list[str]
    end_ids: list[str]
    success_end: str | None
    main_path: set[str]


def _edges(model: ProcessModel) -> set[tuple[str, str]]:
    pairs = {(e.from_id, e.to_id) for e in model.edges}
    for d in model.decisions:
        pairs.update((d.id, b.next_id) for b in d.branches)
    return pairs


def _adjacency(model: ProcessModel):
    succ: dict[str, set[str]] = {}
    pred: dict[str, set[str]] = {}
    for a, b in _edges(model):
        succ.setdefault(a, set()).add(b)
        pred.setdefault(b, set()).add(a)
    return succ, pred


def _reach(seeds: list[str], adj: dict[str, set[str]]) -> dict[str, int]:
    dist = {s: 0 for s in seeds}
    queue = deque(seeds)
    while queue:
        n = queue.popleft()
        for m in adj.get(n, ()):
            if m not in dist:
                dist[m] = dist[n] + 1
                queue.append(m)
    return dist


def propose_success_end(model: ProcessModel) -> str | None:
    succ, _ = _adjacency(model)
    starts = [a.id for a in model.activities if a.kind == "start"]
    dist = _reach(starts, succ)
    best, best_d = None, -1
    for a in model.activities:
        if a.kind == "end" and a.id in dist and dist[a.id] >= best_d:
            best, best_d = a.id, dist[a.id]
    return best


def graph_facts(model: ProcessModel, success_end: str | None = None) -> GraphFacts:
    if success_end is None:
        success_end = propose_success_end(model)
    succ_raw, pred_raw = _adjacency(model)

    nodes = [(n.order, 0, n.id, n.role_id) for n in model.activities
             if n.kind not in ("start", "end")]
    nodes += [(d.order, 1, d.id, d.role_id) for d in model.decisions]
    nodes.sort(key=lambda t: (t[0], t[1]))
    steps = [n[2] for n in nodes]
    actor = {n[2]: n[3] for n in nodes}
    step_set = set(steps)

    succ = {s: set(succ_raw.get(s, ())) for s in steps}
    pred = {s: set(pred_raw.get(s, ())) for s in steps}
    same = {
        s: {n for n in (succ[s] | pred[s]) if n in step_set and actor[n] == actor[s]}
        for s in steps
    }

    start_ids = [a.id for a in model.activities if a.kind == "start"]
    end_ids = [a.id for a in model.activities if a.kind == "end"]
    main_path: set[str] = set()
    if success_end is not None:
        fwd = _reach(start_ids, succ_raw)
        back = _reach([success_end], pred_raw)
        main_path = {s for s in steps if s in fwd and s in back}
    return GraphFacts(steps, succ, pred, actor, same, start_ids, end_ids,
                      success_end, main_path)
