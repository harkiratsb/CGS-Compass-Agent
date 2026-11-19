"""The top follow-up questions: the few assumed answers whose answer could change a result.

run.follow_ups lists every open (assumed) input. Most can't change any call, tier or wave, so
the screen and the export show only the top `n` (10), found by one counterfactual per candidate:
set the answer to the value that would flip it, run the rules (code only) and see what changes.

Ranking: steps whose call could change, then tier, then wave / readiness / posture / redesign
potential; main path first, then map order. After them: the process readiness defaults (they
decide Harden first), documentation checks that could move the band, and last the context
questions the next AI run uses (goal, baselines, volume).

The result is cached on the run (top_questions + top_questions_key) and recomputed only when the
run state changes, so it does not run on every Streamlit rerun.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from framework.graph import graph_facts
from framework.models import A_KEYS, D_KEYS, DIAL_KEYS, R_KEYS, READINESS_KEYS, Answer, FrameworkRun, TopQuestion
from framework.rules import (_locate, _node_name, _put, agent_test_pass, apply_rules, dial, r_answer, yes)
from framework.settings import Settings
from schema import ProcessModel

CONTEXT_NEXT_RUN = ("goal", "baselines", "volume")
NEXT_RUN_LABEL = " (used on the next AI run)"
_CACHE_EXCLUDE = {"top_questions", "top_questions_key", "trace", "artifacts", "summary_text",
                  "first_actions", "follow_ups", "mode"}


@dataclass
class _Cand:
    question: str
    values: dict[str, Any]                 # path -> counterfactual value
    labels: dict[str, str] = field(default_factory=dict)
    step_id: str | None = None


# ------------------------------------------------------------------ cache key

def state_key(run: FrameworkRun, model: ProcessModel, s: Settings, names: list[str] | None = None) -> str:
    h = hashlib.sha256(run.model_dump_json(exclude=_CACHE_EXCLUDE).encode())
    h.update(b"\0" + model.model_dump_json().encode())
    h.update(b"\0" + s.model_dump_json().encode())
    h.update(b"\0" + json.dumps(sorted(names or [])).encode())
    return h.hexdigest()[:20]


def ensure_top_questions(run: FrameworkRun, model: ProcessModel, s: Settings,
                         names: list[str] | None = None, n: int = 10) -> list[TopQuestion]:
    """The cached top questions, recomputed (and stored on `run` in place) only when the state changed."""
    key = f"{state_key(run, model, s, names)}:{n}"
    if run.top_questions_key != key:
        run.top_questions = top_follow_ups(run, model, s, names, n)
        run.top_questions_key = key
    return run.top_questions


# ------------------------------------------------------------------ signature

def _signature(run: FrameworkRun):
    steps = {}
    for sid, st in run.steps.items():
        keep = st.call == "Keep"
        t, r = st.tier, st.recommendation
        steps[sid] = {
            "call": st.call,
            "tier": (t.tier, t.standardize_first, t.approval, t.human_task) if keep and t else None,
            "action": r.action if r else None,
            "wave": r.wave if r else None,
            "harden": r.harden_first if r else None,
            "readiness": st.readiness_min if keep else None,
        }
    return steps, {"posture": tuple(run.posture), "redesign potential": run.redesign_potential,
                   "documentation band": run.gate_band}


def _tier_text(t) -> str:
    if t is None:
        return "not scored"
    tier, std, approval, task = t
    s = ("Standardize first, " if std else "") + tier
    return s + (" + Human approval" if approval else "") + (" + Human task" if task else "")


def _effect(base, new, model: ProcessModel) -> tuple[int, str]:
    """(level, what changes): 3 a call, 2 a tier result, 1 anything else, 0 nothing."""
    (bs, bg), (ns, ng) = base, new
    best, text = 0, ""
    for sid in bs:
        b, x = bs[sid], ns.get(sid, bs[sid])
        if b == x:
            continue
        name = _node_name(model, sid)
        if b["call"] != x["call"]:
            lvl, what = 3, f"{sid} {name}: call {b['call']} → {x['call']}"
        elif b["tier"] != x["tier"]:
            lvl, what = 2, f"{sid} {name}: tier {_tier_text(b['tier'])} → {_tier_text(x['tier'])}"
        elif b["readiness"] != x["readiness"] or b["harden"] != x["harden"]:
            lvl, what = 1, f"{sid} {name}: readiness {b['readiness']} → {x['readiness']}"
        else:
            lvl, what = 1, f"{sid} {name}: {b['action']} → {x['action']}"
        if lvl > best:
            best, text = lvl, what
    if not best:
        for k in bg:
            if bg[k] != ng[k]:
                v0, v1 = bg[k], ng[k]
                if isinstance(v0, tuple):
                    v0, v1 = "; ".join(v0), "; ".join(v1)
                return 1, f"{k}: {v0} → {v1}"
    return best, text


# ------------------------------------------------------------------ counterfactual values

def _flip_y(a: Answer) -> str:
    return "N" if a.value == "Y" else "Y"


def _crossing(key: str, v: int, s: Settings) -> int:
    """The nearest dial value that crosses a tier threshold in settings, else the far end."""
    t = s.tier
    bounds = {
        "rule_clarity": [(t.rules.clarity_min, ">=")],
        "input_structure": [(t.rules.input_min, ">="), (t.agent.input_max, "<=")],
        "variation": [(t.rules.variation_max, "<="), (t.agent.variation_min, ">=")],
        "judgment": [(t.rules.judgment_max, "<="), (t.agent.judgment_min, ">="), (t.human.judgment_min, ">=")],
        "checkability": [(t.agent.checkability_min, ">="), (t.human.checkability_max, "<=")],
        "stakes": [(t.human.stakes, ">="), (t.agent.approval_stakes_min, ">=")],
    }[key]
    options = []
    for th, op in bounds:
        if op == ">=":
            target = th if v < th else th - 1
        else:
            target = th + 1 if v <= th else th
        if 1 <= target <= 5 and target != v:
            options.append(target)
    if not options:
        return 5 if v < 3 else 1
    return min(options, key=lambda x: (abs(x - v), -x))


def _ready_cross(v: Any, s: Settings) -> int:
    v = v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5 else s.defaults.readiness
    return s.readiness.ready_min if v < s.readiness.ready_min else s.readiness.ready_min - 1


def _label(model: ProcessModel, sid: str) -> str:
    for a in model.activities:
        if a.id == sid:
            return f'"{a.name}"'
    for d in model.decisions:
        if d.id == sid:
            return f'the decision "{d.question}"'
    return sid


def _assumed(a: Answer | None) -> bool:
    return a is not None and a.basis == "assumed"


def _step_candidates(run: FrameworkRun, model: ProcessModel, s: Settings, facts) -> list[_Cand]:
    out: list[_Cand] = []
    for sid in facts.steps:
        st = run.steps.get(sid)
        if st is None:
            continue
        label, pre = _label(model, sid), f"steps.{sid}."
        fired = st.call_rule if st.call != "Keep" and st.call_rule in R_KEYS else None
        r_keys = []
        for k in R_KEYS:
            a = st.r_pass1.get(k)
            if not _assumed(a) or r_answer(st, k) is not a:
                continue
            if fired and R_KEYS.index(k) > R_KEYS.index(fired):
                continue                      # a later rule can't fire while an earlier one does
            if st.protected and k not in ("R2", "R7"):
                continue                      # protection keeps the step whatever else is answered
            r_keys.append(k)
        if len(r_keys) == 1:
            a = st.r_pass1[r_keys[0]]
            out.append(_Cand(a.question, {f"{pre}r_pass1.{r_keys[0]}": _flip_y(a)}, step_id=sid))
        elif r_keys:    # one question per step: "should it exist?", tested by letting each rule fire
            out.append(_Cand(f"Should {label} exist? Answer the step-removal tests ({', '.join(r_keys)}).",
                             {f"{pre}r_pass1.{k}": _flip_y(st.r_pass1[k]) for k in r_keys},
                             {f"{pre}r_pass1.{k}": st.r_pass1[k].question for k in r_keys}, sid))
        if _assumed(st.r_move) and yes(r_answer(st, "R4")):
            out.append(_Cand(st.r_move.question,
                             {f"{pre}r_move": "Remove" if st.r_move.value == "Move" else "Move"}, step_id=sid))
        if _assumed(st.accountability):
            out.append(_Cand(st.accountability.question, {f"{pre}accountability": _flip_y(st.accountability)},
                             step_id=sid))
        if _assumed(st.combine_with) and yes(r_answer(st, "R6")):
            others = sorted(facts.same_actor_neighbours.get(sid, set()) - {st.combine_with.value})
            if others:
                out.append(_Cand(st.combine_with.question, {f"{pre}combine_with": others[0]}, step_id=sid))
        if st.call != "Keep" or st.tier is None:
            continue
        d_assumed = [k for k in D_KEYS if _assumed(st.tier1.get(k))]
        if d_assumed:
            v = "Y" if st.tier.standardize_first else "N"
            out.append(_Cand(f"Is {label} done digitally, a written standard way, with findable fields?",
                             {f"{pre}tier1.{k}": v for k in d_assumed},
                             {f"{pre}tier1.{k}": st.tier1[k].question for k in d_assumed}, sid))
        for k in DIAL_KEYS:
            a = st.dials.get(k)
            if _assumed(a):
                out.append(_Cand(a.question, {f"{pre}dials.{k}": _crossing(k, dial(st, k, s), s)}, step_id=sid))
        a_assumed = [k for k in A_KEYS if _assumed(st.agent_test.get(k))]
        if a_assumed:
            target = ({"A1": "N", "A2": "N", "A3": "Y", "A4": "N"} if agent_test_pass(st.agent_test)
                      else {"A1": "Y", "A2": "Y", "A3": "N", "A4": "Y"})
            out.append(_Cand(f"Agent test for {label}: does it need free text or context, does it vary a lot, "
                             "would rules give about 80% of the gain, and is an agent's extra gain worth it?",
                             {f"{pre}agent_test.{k}": target[k] for k in a_assumed},
                             {f"{pre}agent_test.{k}": st.agent_test[k].question for k in a_assumed}, sid))
        for k in READINESS_KEYS:
            a = st.readiness.get(k)
            if _assumed(a):
                out.append(_Cand(a.question, {f"{pre}readiness.{k}": _ready_cross(a.value, s)}, step_id=sid))
    return out


# ------------------------------------------------------------------ the search

def _try(run: FrameworkRun, model: ProcessModel, s: Settings, names, values: dict[str, Any]):
    """Apply the counterfactual answers on `run` in place, run the rules on a copy, put them back."""
    saved = []
    try:
        for path, v in values.items():
            container, key, is_attr = _locate(run, path)
            old = getattr(container, key) if is_attr else container.get(key)
            saved.append((container, key, is_attr, old))
            q = old.question if old is not None else ""
            _put(container, key, is_attr, Answer(value=v, basis="answered", confidence="H", question=q))
        return _signature(apply_rules(run, model, s, names))
    finally:
        for container, key, is_attr, old in reversed(saved):
            _put(container, key, is_attr, old)


def _question(c: _Cand, run: FrameworkRun, effect: str) -> TopQuestion:
    from framework.models import get_answer
    current = {}
    for p in c.values:
        try:
            current[p] = get_answer(run, p).value
        except KeyError:
            current[p] = None
    return TopQuestion(question=c.question or next(iter(c.values)), paths=list(c.values), labels=c.labels,
                       current=current, effect=effect, step_id=c.step_id)


def top_follow_ups(run: FrameworkRun, model: ProcessModel, s: Settings, names: list[str] | None = None,
                   n: int = 10) -> list[TopQuestion]:
    """The top `n` open questions (see the module docstring). Code only; `run` is left as it was."""
    run = run.model_copy(deep=True)
    facts = graph_facts(model, run.success_end)
    base = _signature(apply_rules(run, model, s, names))
    order = {sid: i for i, sid in enumerate(facts.steps)}

    ranked = []
    for seq, c in enumerate(_step_candidates(run, model, s, facts)):
        level, what = _effect(base, _try(run, model, s, names, c.values), model)
        if level:
            ranked.append(((-level, c.step_id not in facts.main_path, order.get(c.step_id, 0), seq), c, what))
    ranked.sort(key=lambda x: x[0])
    out = [_question(c, run, f"Could change {what}") for _, c, what in ranked]

    ready = [k for k, a in run.readiness_defaults.items() if _assumed(a)]
    if ready and len(out) < n:
        c = _Cand("How ready is the process? Rate the readiness tests (1-5).",
                  {f"readiness_defaults.{k}": _ready_cross(run.readiness_defaults[k].value, s) for k in ready},
                  {f"readiness_defaults.{k}": run.readiness_defaults[k].question for k in ready})
        level, what = _effect(base, _try(run, model, s, names, c.values), model)
        if level:
            out.append(_question(c, run, f"Could change {what} (readiness decides Harden first)"))

    for k, a in run.doc_gate.items():
        if len(out) >= n:
            break
        if _assumed(a):
            new = _try(run, model, s, names, {f"doc_gate.{k}": "N" if a.value == "Y" else "Y"})
            if new[1]["documentation band"] != base[1]["documentation band"]:
                out.append(_question(_Cand(a.question, {f"doc_gate.{k}": None}), run,
                                     f"Could move documentation confidence from {base[1]['documentation band']} "
                                     f"to {new[1]['documentation band']}"))

    for k in CONTEXT_NEXT_RUN:
        a = run.context.get(k)
        if len(out) >= n:
            break
        if a is not None and a.basis == "assumed":
            out.append(_question(_Cand(a.question + NEXT_RUN_LABEL, {f"context.{k}": None}), run,
                                 "Changes no result now; the next AI run reads it"))
    return out[:n]
