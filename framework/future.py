"""Future-state process model: the Stage 1 map with the framework's calls applied."""
from __future__ import annotations

from framework.graph import graph_facts
from framework.models import FrameworkRun
from schema import Activity, Edge, ProcessModel, Role

AUTOMATION_ROLE_ID = "R_AUTO"
_MARKER = {"Rules": "rules", "Agent": "agent", "Human approval": "approval", "Human task": "task"}


def build_future_model(model: ProcessModel, run: FrameworkRun, show_removed: bool = False,
                       names: list[str] | None = None) -> tuple[ProcessModel, dict[str, str]]:
    """Return (future model, {node_id: marker}). The input model is not changed.

    With `names`, the text drawn on the diagram (step names, notes, decision questions,
    role names, sticky-note pain points) goes through the same names safety net as the
    exports, so AI-written chain step names can't carry a personal name onto the map."""
    facts = graph_facts(model, run.success_end)
    on_main = set(facts.main_path) | ({facts.success_end} if facts.success_end else set())
    acts = {a.id: a for a in model.activities}
    decs = {d.id: d for d in model.decisions}
    order = {n: x.order for n, x in {**acts, **decs}.items()}

    def call_of(nid: str) -> str | None:
        st = run.steps.get(nid)
        return st.call if st else None

    dropped: dict[str, str | None] = {}   # node id -> replacement (None: nothing to point to)
    markers: dict[str, str] = {}
    chains: dict[str, list] = {}
    combined: dict[str, str] = {}         # dropped node -> combine target (for merging)

    def next_of(nid: str) -> str | None:
        if nid in decs:
            branches = decs[nid].branches
            if not branches:
                return None
            pick = (next((b for b in branches if b.next_id in on_main), None)
                    or next((b for b in branches if b.label.strip().lower() == "yes"), None)
                    or branches[0])
            return pick.next_id
        nexts = sorted({e.to_id for e in model.edges if e.from_id == nid},
                       key=lambda n: (n not in on_main, order.get(n, 0), n))
        return nexts[0] if nexts else None

    for nid in list(acts) + list(decs):
        c = call_of(nid)
        st = run.steps.get(nid)
        bad_target = c == "Combine" and not (st and st.combine_target in order)
        if c == "Remove" or bad_target:
            if show_removed:
                markers[nid] = "removed"
            else:
                dropped[nid] = next_of(nid)
        elif c == "Combine":
            dropped[nid] = st.combine_target
            combined[nid] = st.combine_target
        elif (c == "Keep" and nid in acts and st and st.recommendation
              and st.recommendation.chain):
            chains[nid] = st.recommendation.chain

    def resolve(nid: str | None) -> str | None:
        seen: set[str] = set()
        while nid in dropped:
            if nid in seen:
                return None
            seen.add(nid)
            nid = dropped[nid]
        return nid

    def entry(nid: str | None) -> str | None:
        nid = resolve(nid)
        return f"{nid}.1" if nid in chains else nid

    def exit_(nid: str) -> str:
        return f"{nid}.{len(chains[nid])}" if nid in chains else nid

    role_ids = {r.id for r in model.roles}
    uses_auto = False
    new_acts: list[Activity] = []
    for a in model.activities:
        if a.id in dropped:
            continue
        if a.id not in chains:
            new_acts.append(a.model_copy(deep=True))
            continue
        for i, cs in enumerate(chains[a.id], 1):
            auto = cs.type in ("Rules", "Agent")
            role = AUTOMATION_ROLE_ID if auto else (cs.role_id if cs.role_id in role_ids else a.role_id)
            uses_auto = uses_auto or auto
            n = a.model_copy(deep=True, update={
                "id": f"{a.id}.{i}", "name": f"{a.name} ({cs.name})", "role_id": role})
            if i > 1:
                n.source_refs, n.assumptions, n.notes = [], [], ""
            new_acts.append(n)
            markers[n.id] = _MARKER[cs.type]

    new_decs = [d.model_copy(deep=True) for d in model.decisions if d.id not in dropped]
    for d in new_decs:
        branches = []
        for b in d.branches:
            t = entry(b.next_id)
            if t is not None:
                b.next_id = t
                branches.append(b)
        d.branches = branches

    edges: list[Edge] = []
    seen_e: set[tuple[str, str, str]] = set()

    def add_edge(f: str, t: str | None, label: str = "") -> None:
        if t is None or f == t or (f, t, label) in seen_e:
            return
        seen_e.add((f, t, label))
        edges.append(Edge(from_id=f, to_id=t, label=label))

    for nid, ch in chains.items():
        for i in range(1, len(ch)):
            add_edge(f"{nid}.{i}", f"{nid}.{i + 1}")
    for e in model.edges:
        if e.from_id in dropped:
            continue
        add_edge(exit_(e.from_id), entry(e.to_id), e.label)

    # merge combined steps into their survivor
    by_id = {a.id: a for a in new_acts}
    for src, tgt in combined.items():
        survivor = by_id.get(entry(tgt) or "")
        if survivor is None or src not in acts:
            continue
        a = acts[src]
        survivor.tools += [t for t in a.tools if t not in survivor.tools]
        survivor.source_refs += [r for r in a.source_refs if r not in survivor.source_refs]

    ids = {a.id for a in new_acts} | {d.id for d in new_decs}

    def keep_id(nid: str | None) -> str | None:
        t = entry(nid)
        return t if t in ids else None

    roles = [r.model_copy(deep=True) for r in model.roles]
    if uses_auto:
        roles.append(Role(id=AUTOMATION_ROLE_ID, name="Automation",
                          description="Rules or agent steps run by software"))

    handoffs = []
    for h in model.handoffs:
        at = keep_id(h.at_node_id)
        if at is not None:
            handoffs.append(h.model_copy(update={"at_node_id": at}))
    deps = []
    for dp in model.dependencies:
        nid = keep_id(dp.node_id)
        if nid is None:
            continue
        upd = {"node_id": nid}
        if dp.depends_on in order:
            tgt = keep_id(dp.depends_on)
            if tgt is None:
                continue
            upd["depends_on"] = tgt
        deps.append(dp.model_copy(update=upd))
    pains = []
    for p in model.pain_points:
        if p.node_id is None:
            pains.append(p)
        elif p.node_id not in dropped and keep_id(p.node_id) is not None:
            pains.append(p.model_copy(update={"node_id": keep_id(p.node_id)}))

    future = model.model_copy(deep=True, update={
        "roles": roles, "activities": new_acts, "decisions": new_decs, "edges": edges,
        "handoffs": handoffs, "dependencies": deps, "pain_points": pains})
    if names:
        _scrub_names(future, names)
    return future, markers


def _scrub_names(m: ProcessModel, names: list[str]) -> None:
    """Replace personal names in every label the swimlane and draw.io draw (in place)."""
    from framework.outputs import _clean

    for r in m.roles:
        r.name, r.description = _clean(r.name, names), _clean(r.description, names)
    for a in m.activities:
        a.name, a.notes = _clean(a.name, names), _clean(a.notes, names)
        a.description = _clean(a.description, names)
        a.assumptions = [_clean(x, names) for x in a.assumptions]
    for d in m.decisions:
        d.question = _clean(d.question, names)
        for b in d.branches:
            b.label = _clean(b.label, names)
    for e in m.edges:
        e.label = _clean(e.label, names)
    for p in m.pain_points:
        p.description = _clean(p.description, names)
    m.process.name = _clean(m.process.name, names)
