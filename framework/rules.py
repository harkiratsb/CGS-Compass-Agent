"""Framework rules. Code makes every call; the AI never scores."""
from __future__ import annotations

from framework.graph import GraphFacts
from framework.models import R_KEYS, Answer, Call, StepAssessment
from framework.settings import Settings
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
