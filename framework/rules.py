"""Framework rules. Code makes every call; the AI never scores."""
from __future__ import annotations

import math
from typing import Any

from framework.graph import GraphFacts, graph_facts
from framework.models import (
    A_KEYS, CONTEXT_KEYS, DIAL_KEYS, D_KEYS, GATE_KEYS, READINESS_KEYS, R_KEYS, WORKSHEET_KEYS,
    _STEP_SINGLES, Answer, Call, ChainStep, FollowUp, FrameworkRun, Recommendation,
    StepAssessment, TierResult, allowed_values,
)
from framework.settings import Settings
from ingest.merge import source_coverage
from schema import ProcessModel


# ---------------------------------------------------------------- Part 0: gate

def _grade(share: float) -> str:
    return "Y" if share >= 1 else "Partly" if share >= 0.5 else "N"


def _gate(value: str, reason: str) -> Answer:
    return Answer(value=value, basis="stated", derived=True, reason=reason, confidence="H")


def auto_gate_checks(model: ProcessModel, facts: GraphFacts, names: list[str]) -> dict[str, Answer]:
    out: dict[str, Answer] = {}

    starts = sum(1 for a in model.activities if a.kind == "start")
    ends = sum(1 for a in model.activities if a.kind == "end")
    out["1"] = _gate(
        "Y" if starts >= 1 and ends >= 1 else "N",
        f"{starts} start and {ends} end event(s) on the map",
    )

    lowered = [n.lower() for n in names if n]
    named = [r.name for r in model.roles if any(n in r.name.lower() for n in lowered)]
    out["2"] = _gate(
        "N" if named else "Y",
        f"{len(named)} role name(s) contain a personal name" if named
        else "All swimlanes are roles, not people",
    )

    decisions = model.decisions
    if decisions:
        good = sum(1 for d in decisions
                   if d.question.strip().endswith("?") and all(b.label.strip() for b in d.branches))
        out["4"] = _gate(_grade(good / len(decisions)),
                         f"{good} of {len(decisions)} decisions are a question with labelled branches")
    else:
        out["4"] = _gate("Y", "No decisions on the map")

    problems = model.validate_graph()
    if not problems and facts.success_end is not None:
        v, why = "Y", "Graph is valid and an end is reachable"
    elif not problems:
        v, why = "Partly", "Graph is valid but no end is reachable from a start"
    else:
        v, why = "N", f"{len(problems)} graph problem(s)"
    out["5"] = _gate(v, why)

    acts = [a for a in model.activities if a.kind == "activity"]
    if acts:
        with_tools = sum(1 for a in acts if a.tools)
        out["6"] = _gate(_grade(with_tools / len(acts)),
                         f"{with_tools} of {len(acts)} activities name a tool")
    else:
        out["6"] = _gate("N", "No activities on the map")

    subs = [a for a in model.activities if a.kind == "subprocess"]
    if subs:
        ok = sum(1 for a in subs if a.role_id and (a.description or "").strip())
        v = "Y" if ok == len(subs) else "Partly" if ok else "N"
        out["7"] = _gate(v, f"{ok} of {len(subs)} subprocesses have a role and a description")
    else:
        out["7"] = _gate("Y", "No subprocesses on the map")
    return out


def score_gate(answers: dict[str, Answer], s: Settings) -> tuple[float, str]:
    points = {"Y": 1.0, "Partly": 0.5}
    score = sum(points.get(a.value, 0.0) for a in answers.values())
    band = "High" if score >= s.gate.high else "Medium" if score >= s.gate.medium else "Low"
    return score, band


# ------------------------------------------------- Part 2: should the step exist

def yes(a: Answer | None) -> bool:
    """True only for "Y". "Unknown", "N" and a missing answer all count as No."""
    return a is not None and a.value == "Y"


def part2_call(step: StepAssessment, facts: GraphFacts, is_decision: bool) -> tuple[Call, str | None]:
    """First matching rule R1..R7 decides the call; returns (call, combine_target).

    Uses r_pass2[k] when present, else r_pass1[k]. Decision and accountability
    steps are protected: a Remove/Combine/Move becomes Keep, except when the
    matching rule is R2 (CGS's own example removes a decision-shaped CRM check).

    Side effect: sets step.protected = True whenever the step is a decision or
    has accountability = Y (whether or not protection changed the call).
    """
    step.protected = is_decision or yes(step.accountability)
    step.call_rule = None

    def hit(k: str) -> bool:
        return yes(step.r_pass2.get(k) or step.r_pass1.get(k))

    combine_with = step.combine_with.value if step.combine_with else None
    call: Call = "Keep"
    target: str | None = None
    rule: str | None = None
    for k in R_KEYS:
        if not hit(k):
            continue
        if k in ("R1", "R2", "R5"):
            call = "Remove"
        elif k == "R3":
            call = "Combine"
            target = combine_with if combine_with in facts.steps else None
        elif k == "R4":
            call = "Move" if step.r_move is not None and step.r_move.value == "Move" else "Remove"
        elif k == "R6":
            if combine_with not in facts.same_actor_neighbours.get(step.step_id, set()):
                continue
            call, target = "Combine", combine_with
        elif k == "R7":
            call = "Fix the map"
        rule = k
        break

    if step.protected and call in ("Remove", "Combine", "Move") and rule != "R2":
        return "Keep", None
    step.call_rule = rule
    return call, target


def redesign_potential(calls: list[Call], s: Settings) -> int:
    """Score 1-5 from the percent of steps whose call is not Keep."""
    if not calls:
        return 1
    pct = 100 * sum(1 for c in calls if c != "Keep") / len(calls)
    for score, bound in enumerate(s.redesign.bands, start=1):
        if pct < bound:
            return score
    return len(s.redesign.bands) + 1


# ------------------------------------------------------------ Part 3: the tiers

def dial(step: StepAssessment, key: str, s: Settings) -> int:
    """The dial value if it is an int 1-5 (or a numeric string), else the framework default."""
    default = s.defaults.rule_clarity if key == "rule_clarity" else s.defaults.dial
    a = step.dials.get(key)
    v = a.value if a is not None else None
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            return default
    if isinstance(v, float):
        if not v.is_integer():
            return default
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
        return default
    return v


def agent_test_pass(at: dict[str, Answer]) -> bool:
    return (yes(at.get("A1")) or yes(at.get("A2"))) and not yes(at.get("A3")) and yes(at.get("A4"))


def tier_decision(step: StepAssessment, s: Settings) -> TierResult:
    """First matching rule 1-5 picks the tier. Thresholds come from settings."""
    standardize_first = not all(yes(step.tier1.get(k)) for k in ("D1", "D2", "D3"))
    d = {k: dial(step, k, s) for k in ("rule_clarity", "input_structure", "variation",
                                       "judgment", "checkability", "stakes")}
    t = s.tier

    def result(tier, rule, **kw):
        return TierResult(standardize_first=standardize_first, tier=tier, rule_fired=rule, **kw)

    if d["stakes"] >= t.human.stakes:
        return result("Human", 1)
    if (d["rule_clarity"] >= t.rules.clarity_min and d["input_structure"] >= t.rules.input_min
            and d["variation"] <= t.rules.variation_max and d["judgment"] <= t.rules.judgment_max):
        return result("Rules", 2)
    if d["checkability"] >= t.agent.checkability_min and (
            d["judgment"] >= t.agent.judgment_min or d["input_structure"] <= t.agent.input_max
            or d["variation"] >= t.agent.variation_min):
        if agent_test_pass(step.agent_test):
            return result("Agent", 3, approval=d["stakes"] >= t.agent.approval_stakes_min, error_budget=True)
        return result("Rules", 3, human_task=True)
    if d["judgment"] >= t.human.judgment_min and d["checkability"] <= t.human.checkability_max:
        return result("Human", 4)
    return result("Mixed", 5)


# -------------------------------------------------------- Part 4: readiness

def _score(a: Answer | None) -> int | None:
    v = a.value if a is not None else None
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
        return None
    return v


def readiness_min(step: StepAssessment, defaults: dict[str, Answer], s: Settings) -> int:
    """Lowest of the five tests; per-step override, else process default, else the framework default."""
    vals = []
    for k in READINESS_KEYS:
        v = _score(step.readiness.get(k) or defaults.get(k))
        vals.append(s.defaults.readiness if v is None else v)
    return min(vals)


# ---------------------------------------------------------- Part 5: recommend

_CHANGE_CALLS = ("Remove", "Combine", "Move", "Reimagine")


def _tier_label(t: TierResult) -> str:
    if t.tier == "Rules" and t.human_task:
        return "Rules + Human task"
    if t.tier == "Agent" and t.approval:
        return "Agent + Human approval"
    if t.tier == "Mixed":
        return "Mixed: split the step, or write the rule down and rescore"
    return t.tier


def _chain(t: TierResult) -> list[ChainStep]:
    if t.tier == "Rules":
        types = ["Rules"] + (["Human task"] if t.human_task else [])
    elif t.tier == "Agent":
        types = ["Agent"] + (["Human approval"] if t.approval else [])
    else:  # Human, and Mixed until it is split or rescored
        types = ["Human task"]
    return [ChainStep(name=x, type=x) for x in types]


def chain_compatible(chain: list[ChainStep], code: list[ChainStep]) -> bool:
    """An AI chain may refine code's chain: it keeps every type code chose and adds no Agent."""
    types, base = {x.type for x in chain}, {x.type for x in code}
    return bool(chain) and base <= types and ("Agent" in base or "Agent" not in types)


def recommend(step: StepAssessment, s: Settings) -> Recommendation:
    if step.call == "Fix the map":
        return Recommendation(action="Ask the owner, then rescore", wave=0)
    if step.call in _CHANGE_CALLS:
        return Recommendation(action=step.call, wave=0)
    t = step.tier
    if t is None:
        return Recommendation(action="Keep", wave=None)
    label = _tier_label(t)
    tier_wave = {"Rules": 2, "Agent": 3}.get(t.tier)
    if t.standardize_first:
        action, wave = f"Standardize first, then {label}", 1
    else:
        action, wave = label, tier_wave
    ready = step.readiness_min if step.readiness_min is not None else s.defaults.readiness
    harden = t.tier in ("Rules", "Agent") and ready < s.readiness.ready_min
    chain = _chain(t)
    if step.ai_chain and chain_compatible(step.ai_chain, chain):
        chain = [c.model_copy() for c in step.ai_chain]
    return Recommendation(action=action, wave=wave, harden_first=harden, chain=chain)


def posture(run: FrameworkRun, facts: GraphFacts) -> list[str]:
    if run.gate_band == "Low":
        line = "Document first"
    elif any(set(mc.step_ids) & facts.main_path for mc in run.map_checks):
        line = "Fix the map"
    elif run.redesign_potential >= 4:
        line = "Redesign first"
    elif run.redesign_potential == 3:
        line = "Redesign the flagged segments, then standardize and automate the rest"
    else:
        line = "Incremental"
    out = [line]
    if any(st.tier is not None and st.tier.tier in ("Rules", "Agent")
           and st.recommendation is not None and st.recommendation.harden_first
           for st in run.steps.values()):
        out.append("Harden first")
    return out


def waves(run: FrameworkRun) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {w: [] for w in range(4)}
    for sid, st in run.steps.items():
        r = st.recommendation
        if r is not None and r.wave in out:
            out[r.wave].append(sid)
    return out


_STEP_FAMILIES = ("worksheet", "r_pass1", "r_pass2", "r_move", "accountability", "combine_with",
                  "tier1", "dials", "agent_test", "readiness")


def _step_answers(st: StepAssessment):
    """(path suffix, Answer) for every answer on a step, in a stable family order."""
    for fam in _STEP_FAMILIES:
        val = getattr(st, fam)
        if isinstance(val, dict):
            for k, a in val.items():
                yield f"{fam}.{k}", a
        elif val is not None:
            yield fam, val


def follow_ups(run: FrameworkRun, order: list[str] | None = None) -> list[FollowUp]:
    """Every assumed answer: process level first, then steps in `order` (default: run order)."""
    out: list[FollowUp] = []

    def add(path: str, a: Answer):
        if a.basis == "assumed":
            out.append(FollowUp(path=path, question=a.question or f"Please confirm {path}",
                                current=a.value))

    for fam in ("doc_gate", "context", "readiness_defaults"):
        for k, a in getattr(run, fam).items():
            add(f"{fam}.{k}", a)
    ids = [i for i in (order or []) if i in run.steps]
    ids += [i for i in run.steps if i not in ids]
    for sid in ids:
        for suffix, a in _step_answers(run.steps[sid]):
            add(f"steps.{sid}.{suffix}", a)
    return out


# ------------------------------------------------------------ the code pass

_MEDIUM_FLAG = "doc confidence Medium"


_CAP_PREFIX = "confidence capped: "


def _cap_m(a: Answer, why: str) -> None:
    """Lower H to M and record the original level in a flag, so the next pass can undo it."""
    if a.confidence == "H":
        a.flags.append(f"{_CAP_PREFIX}{why} (was {a.confidence})")
        a.confidence = "M"


def _uncap(a: Answer) -> None:
    """Drop cap flags from a previous pass; restore the recorded confidence only if the
    answer is still the capped one (stated, at M). A later downgrade or a person's answer wins."""
    caps = [f for f in a.flags if f.startswith(_CAP_PREFIX)]
    if not caps:
        return
    original = caps[0].rsplit("(was ", 1)[-1].rstrip(")")
    if a.basis == "stated" and a.confidence == "M" and original in ("H", "M", "L"):
        a.confidence = original
    a.flags = [f for f in a.flags if not f.startswith(_CAP_PREFIX)]


def apply_rules(run: FrameworkRun, model: ProcessModel, s: Settings,
                names: list[str] | None = None) -> FrameworkRun:
    """The whole code pass. Pure: works on a deep copy and returns it."""
    run = run.model_copy(deep=True)
    facts = graph_facts(model, run.success_end)
    run.success_end = facts.success_end
    ids = [sid for sid in facts.steps if sid in run.steps]
    decision_ids = {d.id for d in model.decisions}

    # Part 0: auto checks never overwrite a person's answer
    for k, a in auto_gate_checks(model, facts, names or []).items():
        if k not in run.doc_gate or run.doc_gate[k].basis != "answered":
            run.doc_gate[k] = a
    run.gate_score, run.gate_band = score_gate(run.doc_gate, s)

    # Medium: keep the values, flag them and cap confidence at M (flag is re-derived each pass)
    for st in run.steps.values():
        for _, a in _step_answers(st):
            _uncap(a)
            if _MEDIUM_FLAG in a.flags:
                a.flags = [f for f in a.flags if f != _MEDIUM_FLAG]
            if run.gate_band == "Medium" and a.basis != "answered":
                a.flags.append(_MEDIUM_FLAG)
                _cap_m(a, "doc Medium")

    # single-source cap
    cov = source_coverage(model)
    for sid, st in run.steps.items():
        if cov.get(sid, 0) < 2:
            for _, a in _step_answers(st):
                if a.basis == "stated" and not a.derived:
                    _cap_m(a, "single source")

    # steps no longer on the map keep no stale results
    for sid, st in run.steps.items():
        if sid not in facts.steps:
            st.call = st.call_rule = st.combine_target = st.tier = st.readiness_min = st.recommendation = None

    # Part 2
    for sid in ids:
        st = run.steps[sid]
        st.call, st.combine_target = part2_call(st, facts, sid in decision_ids)
    run.redesign_potential = redesign_potential([run.steps[i].call for i in ids], s)

    # Parts 3-5 (Low gate stops scoring)
    for sid in ids:
        st = run.steps[sid]
        if run.gate_band == "Low":
            st.tier = st.readiness_min = st.recommendation = None
            continue
        st.tier = tier_decision(st, s)
        st.readiness_min = readiness_min(st, run.readiness_defaults, s)
        st.recommendation = recommend(st, s)
        sop = run.doc_gate.get("8")
        if (st.tier.tier in ("Rules", "Agent") and st.recommendation.wave != 0
                and (sop is None or sop.value != "Y")):
            st.recommendation.action += "; Write the SOP"

    # made_unnecessary_by holds only while the target is a kept Agent step that is not itself
    # made unnecessary (checked against this pass's values, so mutual pairs clear both)
    marked = {sid for sid, st in run.steps.items() if st.made_unnecessary_by}
    for sid in marked:
        st, target = run.steps[sid], run.steps.get(run.steps[sid].made_unnecessary_by)
        if (target is None or target.tier is None or target.tier.tier != "Agent"
                or target.call != "Keep" or st.made_unnecessary_by in marked):
            st.made_unnecessary_by = None

    run.posture = posture(run, facts)
    run.waves = waves(run)
    run.follow_ups = follow_ups(run, facts.steps)
    return run


# ------------------------------------------------------------ answering follow-ups

_RUN_FAMILIES = {
    "doc_gate": GATE_KEYS, "context": CONTEXT_KEYS, "readiness_defaults": READINESS_KEYS,
}
_STEP_FAMILY_KEYS = {
    "worksheet": WORKSHEET_KEYS, "r_pass1": R_KEYS, "r_pass2": R_KEYS, "tier1": D_KEYS,
    "dials": DIAL_KEYS, "agent_test": A_KEYS, "readiness": READINESS_KEYS,
}
_HISTORY_FIELDS = ("value", "basis", "reason", "confidence", "question", "derived")


def _locate(run: FrameworkRun, path: str):
    """Return (container, key, is_attr) for a valid answer path, else KeyError.

    A dict-family key may not exist yet but must be a known key; a single step field may be None."""
    parts = path.split(".")
    if parts[0] in _RUN_FAMILIES and len(parts) == 2:
        d = getattr(run, parts[0])
        if parts[1] in d or parts[1] in _RUN_FAMILIES[parts[0]]:
            return d, parts[1], False
    elif parts[0] == "steps" and len(parts) >= 3 and parts[1] in run.steps:
        st = run.steps[parts[1]]
        if len(parts) == 4 and parts[2] in _STEP_FAMILY_KEYS:
            d = getattr(st, parts[2])
            if parts[3] in d or parts[3] in _STEP_FAMILY_KEYS[parts[2]]:
                return d, parts[3], False
        elif len(parts) == 3 and parts[2] in _STEP_SINGLES:
            return st, parts[2], True
    raise KeyError(path)


def _clean_answer(path: str, value: Any) -> Any:
    allowed = allowed_values(path)
    if allowed is None:
        if isinstance(value, bool) or value is None:
            raise ValueError("An answer can't be empty.")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError("An answer can't be empty.")
    shown = ", ".join(str(a) for a in allowed)
    bad = ValueError(f"{value} isn't allowed here. Choose one of: {shown}")
    if isinstance(value, bool):
        raise bad
    if all(isinstance(a, int) for a in allowed):
        try:
            num = float(value.strip()) if isinstance(value, str) else float(value)
        except (TypeError, ValueError, OverflowError):
            raise bad
        if not math.isfinite(num) or num != int(num) or int(num) not in allowed:
            raise bad
        return int(num)
    if isinstance(value, str):
        v = value.strip()
        for a in allowed:
            if v == a or v.lower() == str(a).lower():
                return a
    raise bad


def _put(container, key: str, is_attr: bool, answer: Answer | None) -> None:
    if is_attr:
        setattr(container, key, answer)
    elif answer is None:
        container.pop(key, None)
    else:
        container[key] = answer


def answer_and_rescore(run: FrameworkRun, model: ProcessModel, path: str, value: Any,
                       s: Settings, reason: str = "Answered in the app") -> FrameworkRun:
    """Record a person's answer (basis answered, confidence H), keep the old one in history,
    and re-run the rules. No AI call. The run passed in is never modified."""
    run = run.model_copy(deep=True)
    container, key, is_attr = _locate(run, path)
    value = _clean_answer(path, value)
    old = getattr(container, key) if is_attr else container.get(key)
    if old is None:
        history = [{"absent": True}]
    else:
        entry = {f: getattr(old, f) for f in _HISTORY_FIELDS}
        entry["quotes"] = [q.model_dump() for q in old.quotes]
        history = [*old.history, entry]
    _put(container, key, is_attr,
         Answer(value=value, basis="answered", reason=reason, confidence="H", history=history))
    return apply_rules(run, model, s)


def undo_answer(run: FrameworkRun, model: ProcessModel, path: str, s: Settings) -> FrameworkRun:
    """Put back the answer that was replaced last. ValueError if there is nothing to undo."""
    run = run.model_copy(deep=True)
    container, key, is_attr = _locate(run, path)
    cur = getattr(container, key) if is_attr else container.get(key)
    if cur is None or not cur.history:
        raise ValueError("There is nothing to undo here.")
    entry, rest = cur.history[-1], cur.history[:-1]
    if entry.get("absent"):
        _put(container, key, is_attr, None)
    else:
        fields = {f: entry[f] for f in _HISTORY_FIELDS if f in entry}
        restored = Answer(**fields, quotes=entry.get("quotes", []), history=rest)
        _put(container, key, is_attr, restored)
    return apply_rules(run, model, s)
