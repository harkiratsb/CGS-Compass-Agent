"""
Post-extraction consolidation: merge near-duplicate activities (same role, very similar
name) that slipped through, re-pointing edges/decisions/pain points at the survivor.

Also computes per-node source coverage (how many distinct files cite it), which the UI
uses to show "corroborated by 2 sources" vs "single source".
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict

from schema import ProcessModel


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def dedupe_activities(model: ProcessModel, threshold: float = 0.9) -> ProcessModel:
    survivors = []
    remap: Dict[str, str] = {}
    for a in model.activities:
        if a.kind in ("start", "end"):
            survivors.append(a)
            continue
        hit = next(
            (s for s in survivors
             if s.role_id == a.role_id and s.kind == a.kind and _similar(s.name, a.name) >= threshold),
            None,
        )
        if hit:
            remap[a.id] = hit.id
            # merge evidence and metadata into survivor
            seen = {(r.file, r.location) for r in hit.source_refs}
            hit.source_refs += [r for r in a.source_refs if (r.file, r.location) not in seen]
            hit.tools = sorted(set(hit.tools) | set(a.tools))
            hit.assumptions = sorted(set(hit.assumptions) | set(a.assumptions))
            if not hit.description and a.description:
                hit.description = a.description
        else:
            survivors.append(a)

    if not remap:
        return model

    def rm(x: str) -> str:
        return remap.get(x, x)

    model.activities = survivors
    for e in model.edges:
        e.from_id, e.to_id = rm(e.from_id), rm(e.to_id)
    # drop self-loops created by merging
    model.edges = [e for e in model.edges if e.from_id != e.to_id]
    for d in model.decisions:
        for b in d.branches:
            b.next_id = rm(b.next_id)
    for h in model.handoffs:
        h.at_node_id = rm(h.at_node_id)
    for dep in model.dependencies:
        dep.node_id = rm(dep.node_id)
    for p in model.pain_points:
        if p.node_id:
            p.node_id = rm(p.node_id)
    return model


def source_coverage(model: ProcessModel) -> Dict[str, int]:
    """node id -> number of distinct source files citing it."""
    cov: Dict[str, int] = {}
    for a in model.activities:
        cov[a.id] = len({r.file for r in a.source_refs})
    for d in model.decisions:
        cov[d.id] = len({r.file for r in d.source_refs})
    return cov
