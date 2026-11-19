"""Exports: the step table (rows, CSV), the summary and the follow-up questions (Markdown).

Every number comes from the run. A missing fact is written as "Gap: <what's missing>".
Text passes through the names safety net so only roles appear.
"""
from __future__ import annotations

import csv
import io

from framework.graph import graph_facts
from framework.models import R_KEYS, Answer, FrameworkRun, StepAssessment
from framework.rules import _step_answers, _tier_label, r_answer
from handoff import _scrub, with_nicknames
from schema import ProcessModel

STEP_TABLE_COLUMNS = ["step_id", "step", "lane", "call", "tier_result", "future_chain", "wave",
                      "readiness", "reason", "confidence", "gaps"]

_TIER_RULES = {
    1: "Tier rule 1: stakes are high, a human decides",
    2: "Tier rule 2: clear rules, structured input",
    3: "Tier rule 3: checkable work with judgment or messy input",
    4: "Tier rule 4: judgment that is hard to check",
    5: "Tier rule 5: no tier fits, split the step or write the rule down",
}
_CONF_ORDER = {"L": 0, "M": 1, "H": 2}


def _clean(text: str, names: list[str] | None) -> str:
    return _scrub(text, with_nicknames(list(names))) if names else text


def _step_label(model: ProcessModel, sid: str) -> str:
    for a in model.activities:
        if a.id == sid:
            return a.name
    for d in model.decisions:
        if d.id == sid:
            return d.question
    return sid


def _lane(model: ProcessModel, facts, sid: str) -> str:
    try:
        return model.role_name(facts.actor[sid])
    except Exception:
        return ""


def _deciding(st: StepAssessment, model: ProcessModel | None = None) -> tuple[str, str]:
    """(reason, confidence) of the answer that decided the call."""
    gap = "Gap: no reason recorded"
    if st.call_rule == "Agent" and st.made_unnecessary_by:
        by = st.made_unnecessary_by
        name = _step_label(model, by) if model is not None else by
        return f"Made unnecessary by Agent step {by} ({name}), from the AI loop pass", "L"
    if st.call and st.call != "Keep":
        k = st.call_rule
        a = r_answer(st, k) if k else None
        if k == "R4" and st.r_move is not None:
            a = st.r_move
        if a is None:
            return gap, "L"
        return (f"{k}: {a.reason}" if a.reason else gap), a.confidence
    if st.tier is None:
        return gap, "L"
    rule = _TIER_RULES.get(st.tier.rule_fired, f"Tier rule {st.tier.rule_fired}")
    answers = [*st.dials.values(), *st.tier1.values()]
    conf = min((a.confidence for a in answers), key=_CONF_ORDER.get) if answers else "L"
    return rule, conf


def _gaps(st: StepAssessment) -> int:
    return sum(1 for _, a in _step_answers(st) if a.basis == "assumed")


def step_table(run: FrameworkRun, model: ProcessModel, names: list[str] | None = None) -> list[dict]:
    facts = graph_facts(model, run.success_end)
    rows = []
    for sid in facts.steps:
        st = run.steps.get(sid)
        if st is None:
            continue
        rec, t = st.recommendation, st.tier
        if st.call and st.call != "Keep":
            tier_result = chain = ""
        elif t is None:
            tier_result, chain = "Gap: not scored", ""
        else:
            tier_result = ("Standardize first → " if t.standardize_first else "") + _tier_label(t)
            chain = " → ".join(c.name for c in rec.chain) if rec else ""
        reason, conf = _deciding(st, model)
        row = {"step_id": sid, "step": _step_label(model, sid), "lane": _lane(model, facts, sid),
               "call": st.call or "", "tier_result": tier_result, "future_chain": chain,
               "wave": rec.wave if rec and rec.wave is not None else "",
               "readiness": st.readiness_min if st.readiness_min is not None else "",
               "reason": reason, "confidence": conf, "gaps": _gaps(st)}
        rows.append({k: _clean(v, names) if isinstance(v, str) else v for k, v in row.items()})
    return rows


def step_table_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=STEP_TABLE_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _val(a: Answer | None, what: str) -> str:
    if a is None or a.value in (None, ""):
        return f"Gap: {what}"
    return str(a.value) + ("" if a.basis != "assumed" else " (assumed)")


def summary_markdown(run: FrameworkRun, model: ProcessModel, names: list[str] | None = None) -> str:
    rows = step_table(run, model)
    out: list[str] = []

    def section(title: str, lines: list[str]):
        out.append(f"## {title}\n")
        out.extend(lines or ["Gap: nothing recorded"])
        out.append("")

    ctx = run.context
    section("Outcome and goal", [
        f"- Outcome: {_val(ctx.get('outcome'), 'outcome not stated')}",
        f"- Goal: {_val(ctx.get('goal'), 'goal not stated')}",
        f"- Measures: {_val(ctx.get('measures'), 'how success is measured')}",
        f"- Baselines: {_val(ctx.get('baselines'), 'baseline values')}",
        f"- Volume: {_val(ctx.get('volume'), 'how often the process runs')}",
    ])
    if run.doc_gate:
        section("Documentation confidence",
                [f"{run.gate_band} (score {run.gate_score:g} of {len(run.doc_gate)} checks)"])
    else:
        section("Documentation confidence", ["Gap: documentation checks not run"])
    section("Open map questions",
            [f"- {mc.question} (steps {', '.join(mc.step_ids)})" for mc in run.map_checks]
            or ["None found"])
    section("Redesign potential", [f"{run.redesign_potential} of 5"])

    tiers: dict[str, list[str]] = {}
    for r in rows:
        key = r["tier_result"] or (f"Not scored ({r['call']})" if r["call"] else "Gap: no call")
        tiers.setdefault(key, []).append(r["step_id"])
    section("Steps by tier", [f"- {k}: {len(v)} ({', '.join(v)})" for k, v in tiers.items()])

    ready: dict[object, list[str]] = {}
    for r in rows:
        if r["readiness"] != "":
            ready.setdefault(r["readiness"], []).append(r["step_id"])
    section("Readiness by test", [f"- Weakest test scores {k}: {len(v)} steps ({', '.join(v)})"
                                  for k, v in sorted(ready.items())]
            or ["Gap: readiness not scored"])
    section("Posture", [f"- {p}" for p in run.posture] or ["Gap: posture not set"])
    section("Waves", [f"- Wave {w}: {', '.join(ids)}" for w, ids in sorted(run.waves.items()) if ids]
            or ["Gap: no waves set"])
    section("First three actions", [f"{i}. {a}" for i, a in enumerate(run.first_actions[:3], 1)]
            or ["Gap: first actions not written"])
    gaps = sum(r["gaps"] for r in rows)
    gap_lines = [f"- {gaps} inputs are assumed, not evidenced ({len(run.follow_ups)} follow-up questions)"]
    if not run.summary_text:
        gap_lines.append("- Gap: written summary missing")
    section("Gaps", gap_lines)
    if run.summary_text:
        out.append(run.summary_text + "\n")
    return _clean("\n".join(out).rstrip() + "\n", names)


def follow_up_groups(run: FrameworkRun, model: ProcessModel, dedupe: bool = True) -> list[tuple[str, str, list]]:
    """Follow-ups grouped for sending: [(section, group title, [FollowUp])].

    Sections, in order: "Start here" (process facts and documentation checks), "Steps on the
    main path", "Other steps"; each step's questions sit under its name. With `dedupe`, questions
    repeated word for word (two steps with the same name) are listed once, as in the Markdown."""
    facts = graph_facts(model, run.success_end)
    seen: set[str] = set()
    process: list = []
    by_step: dict[str, list] = {}
    for f in run.follow_ups:
        if dedupe and f.question in seen:
            continue
        seen.add(f.question)
        parts = f.path.split(".")
        if parts[0] == "steps":
            by_step.setdefault(parts[1], []).append(f)
        else:
            process.append(f)
    order = ([s for s in facts.steps if s in by_step and s in facts.main_path]
             + [s for s in facts.steps if s in by_step and s not in facts.main_path]
             + [s for s in by_step if s not in facts.steps])
    groups = [("Start here", "Process facts and documentation", process)] if process else []
    for sid in order:
        section = "Steps on the main path" if sid in facts.main_path else "Other steps"
        groups.append((section, f"{sid}: {_step_label(model, sid)}", by_step[sid]))
    return groups


def follow_ups_markdown(run: FrameworkRun, model: ProcessModel, names: list[str] | None = None) -> str:
    out = ["# Follow-up questions\n"]

    def line(f):
        return f"- {f.question} (current: {f.current if f.current is not None else 'none'}; answer goes in `{f.path}`)"

    groups = follow_up_groups(run, model)
    if not groups:
        out.append("None: every input has evidence or an answer.")
    last = None
    for section, title, items in groups:
        if section != last:
            out.append(f"\n## {section}\n")
            last = section
        out.append(f"\n### {title}\n")
        out.extend(map(line, items))
    return _clean("\n".join(out) + "\n", names)
