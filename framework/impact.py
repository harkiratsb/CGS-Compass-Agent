"""Expected impact of the future-state calls: time saved, roles freed, decisions and pain points.

Everything is computed from the run and the Stage 1 map. Monthly figures need a volume the
user stated or answered that parses to a number per period; otherwise they stay None with a gap.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from framework.future import AUTOMATION_ROLE_ID, build_future_model
from framework.graph import _adjacency, graph_facts
from framework.models import FrameworkRun
from schema import ProcessModel

GAP_VOLUME = "Gap: answer the volume question to see hours saved per month"
WEEKS_PER_MONTH = 4.33
_PERIODS = {"week": WEEKS_PER_MONTH, "month": 1.0, "year": 1 / 12, "quarter": 1 / 3}
_VOL_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)[^\d/]*?(?:\b(?:a|an|per|each|every)\b|/)\s*(week|month|year|quarter)\b",
                     re.IGNORECASE)


def parse_volume(value) -> float | None:
    """Leads per month from text like "40 leads a month", "10 per week", "500 a year"; else None."""
    if not isinstance(value, str):
        return None
    m = _VOL_RE.search(value)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return n * _PERIODS[m.group(2).lower()]


class StepMinutes(BaseModel):
    step_id: str
    label: str
    minutes: float
    conditional: bool = False


class FreedRole(BaseModel):
    role_id: str
    name: str
    steps_before: int
    removed_minutes: float


class Impact(BaseModel):
    total_minutes: float = 0
    steps_with_minutes: int = 0
    steps_without_minutes: int = 0
    stated_minutes: float = 0
    estimated_minutes: float = 0
    removed_minutes: float = 0
    always_minutes: float = 0          # removed steps on every path from start to the success end
    conditional_minutes: float = 0     # removed steps behind a decision branch or off the main path
    removed_steps: list[StepMinutes] = Field(default_factory=list)
    merged_steps: list[StepMinutes] = Field(default_factory=list)
    merged_minutes: float = 0          # merged, time not removed
    freed_roles: list[FreedRole] = Field(default_factory=list)
    decisions_before: int = 0
    decisions_after: int = 0
    pains_total: int = 0
    pains_resolved: int = 0            # on removed or moved steps
    pains_open: int = 0
    leads_per_month: float | None = None
    hours_per_month: float | None = None          # all removed minutes: an upper bound if conditional > 0
    hours_per_month_always: float | None = None
    volume_gap: str = ""


def _always_nodes(model: ProcessModel, run: FrameworkRun, candidates: set[str]) -> set[str]:
    """Candidates that lie on every path from a start to the success end."""
    facts = graph_facts(model, run.success_end)
    end = facts.success_end
    if end is None or not facts.start_ids:
        return set()
    succ, _ = _adjacency(model)
    out = set()
    for n in candidates & facts.main_path:
        seen, stack = set(facts.start_ids), list(facts.start_ids)
        while stack:
            x = stack.pop()
            for y in succ.get(x, ()):
                if y != n and y not in seen:
                    seen.add(y)
                    stack.append(y)
        if end not in seen:
            out.add(n)
    return out


def impact(run: FrameworkRun, model: ProcessModel) -> Impact:
    nodes = {a.id: a for a in model.activities} | {d.id: d for d in model.decisions}

    def label(sid: str) -> str:
        n = nodes.get(sid)
        return getattr(n, "name", None) or getattr(n, "question", sid)

    def mins(sid: str) -> float | None:
        n = nodes.get(sid)
        return n.human_minutes_per_cycle if n is not None else None

    imp = Impact()
    ids = [s for s in run.steps if s in nodes]
    for sid in ids:
        m = mins(sid)
        if m is None:
            imp.steps_without_minutes += 1
            continue
        imp.steps_with_minutes += 1
        imp.total_minutes += m
        if nodes[sid].sizing_basis.human_minutes_per_cycle == "stated":
            imp.stated_minutes += m
        else:
            imp.estimated_minutes += m

    removed = [s for s in ids if run.steps[s].call == "Remove"]
    always = _always_nodes(model, run, set(removed))
    for sid in removed:
        m = mins(sid) or 0.0
        imp.removed_steps.append(StepMinutes(step_id=sid, label=label(sid), minutes=m,
                                             conditional=sid not in always))
        imp.removed_minutes += m
        if sid in always:
            imp.always_minutes += m
        else:
            imp.conditional_minutes += m
    for sid in ids:
        if run.steps[sid].call == "Combine":
            m = mins(sid) or 0.0
            imp.merged_steps.append(StepMinutes(step_id=sid, label=label(sid), minutes=m))
            imp.merged_minutes += m

    # roles with no step left in the future model (the Automation lane does not count as a human lane)
    future, _ = build_future_model(model, run)
    facts = graph_facts(model, run.success_end)
    left: dict[str, int] = {}
    for n in [*future.activities, *future.decisions]:
        if getattr(n, "kind", "activity") not in ("start", "end"):
            left[n.role_id] = left.get(n.role_id, 0) + 1
    before: dict[str, int] = {}
    for sid in facts.steps:
        before[facts.actor[sid]] = before.get(facts.actor[sid], 0) + 1
    for r in model.roles:
        if r.is_external or r.id == AUTOMATION_ROLE_ID or not before.get(r.id) or left.get(r.id):
            continue
        rm = sum(s.minutes for s in imp.removed_steps if facts.actor.get(s.step_id) == r.id)
        imp.freed_roles.append(FreedRole(role_id=r.id, name=r.name, steps_before=before[r.id],
                                         removed_minutes=rm))

    imp.decisions_before = len(model.decisions)
    imp.decisions_after = len(future.decisions)
    imp.pains_total = len(model.pain_points)
    imp.pains_resolved = sum(1 for p in model.pain_points
                             if p.node_id in run.steps and run.steps[p.node_id].call in ("Remove", "Move"))
    imp.pains_open = imp.pains_total - imp.pains_resolved

    vol = run.context.get("volume")
    per_month = parse_volume(vol.value) if vol is not None and vol.basis in ("stated", "answered") else None
    if per_month is None:
        imp.volume_gap = GAP_VOLUME
    else:
        imp.leads_per_month = per_month
        imp.hours_per_month = imp.removed_minutes * per_month / 60
        imp.hours_per_month_always = imp.always_minutes * per_month / 60
    return imp
