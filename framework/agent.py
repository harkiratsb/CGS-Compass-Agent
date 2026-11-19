"""
Phase 3 pipeline: the CGS Process Improvement Framework run over a reviewed Stage 1 map.

The AI only answers the framework's judgment questions (as `Answer`s with quotes).
Code (framework/rules.py) makes every call, applies every threshold and computes every score.

  1 Load (code) -> 2 Doc gate -> 3 Context reader -> 4 Step Removal pass 1 -> 5 pass 2 (context)
  -> 6 Disambiguation -> 6b Step Removal loop -> 7 Readiness -> 8 Verifier (review model)
  -> 9 Recommend (code) -> 10 Workflow Documentation & Specification -> 11 Summary

Every step is an AgentStep in run.trace; its output JSON is kept in run.artifacts[step id].
Live runs are cached in .cache/framework_<hash>.json; the demo replays
demo_data/framework_run_sales_process.json when its recorded input hash matches.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from framework.graph import GraphFacts, graph_facts
from framework.models import (
    _A_Q, _CONTEXT_Q, _D_Q, _DIAL_Q, _GATE_Q, _R_Q, _READINESS_Q, _WORKSHEET_Q, A_KEYS, D_KEYS,
    DIAL_KEYS, R_KEYS, R_MOVE, READINESS_KEYS, SCORE, YN, YNU, YPN, Answer, ChainStep, FrameworkRun,
    MapCheck, assumed, blank_run, get_answer,
)
from framework.rules import _step_answers, answered_answers, apply_rules, carry_answers, chain_compatible
from framework.settings import Settings, load_settings
from cgs_tools import tags_for
from ingest import agent as _stage1
from ingest.agent import REVIEW_MODEL, Truncated
from ingest.extract import MODEL, _strip_fences
from ingest.merge import source_coverage
from ingest.parse import ParsedFile, to_prompt_text
from schema import AgentRun, AgentStep, ProcessModel, SourceRef

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
DEMO_RUN = ROOT / "demo_data" / "framework_run_sales_process.json"
NO_KEY = "No API key and no saved run for this map. Set ANTHROPIC_API_KEY in .env."

_call = _stage1._call   # module-level so tests can monkeypatch framework.agent._call

TYPE_TODAY = ("Rules", "Agent", "Human approval", "Human task")
_CAP_PREFIX = "confidence capped:"
_HINTS = {"delay": "R4 (mostly waiting)", "manual": "R3 (re-keys information)",
          "rework": "R5 (fixes an earlier problem)", "ownership": "R2 (exists only for a handoff)",
          "missing-rule": "R7 / Rule clarity"}

# --------------------------------------------------------------------------- answers


def _quotes(raw) -> list[SourceRef]:
    out = []
    for q in raw if isinstance(raw, list) else []:
        if isinstance(q, dict) and str(q.get("quote") or "").strip():
            out.append(SourceRef(file=str(q.get("file") or ""), location=str(q.get("location") or ""),
                                 quote=str(q["quote"])))
    return out


def _coerce(v, allowed: tuple | None) -> tuple[Any, bool]:
    """(value, ok). Numeric strings and integral floats become ints; Y/N match case-insensitively."""
    if allowed is None:
        return v, True
    if isinstance(v, bool):
        return v, False
    if isinstance(v, str):
        hit = next((a for a in allowed if isinstance(a, str) and a.lower() == v.strip().lower()), None)
        if hit is not None:
            return hit, True
        try:
            v = float(v.strip())
        except ValueError:
            return v, False
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v, v in allowed


def parse_answers(raw: dict, keys: list[str], allowed: tuple | None, defaults: dict[str, Any],
                  questions: dict[str, str]) -> dict[str, Answer]:
    """AI output -> Answers. Missing, null or invalid -> assumed(default). The AI can only say
    "stated" (needs a quote) or "assumed"; an assumed answer takes the framework default
    (for free text the AI's suggestion is kept in `reason`)."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Answer] = {}
    for k in keys:
        q, d = questions.get(k, ""), defaults.get(k)
        item = raw.get(k)
        item = item if isinstance(item, dict) else {"value": item}
        if item.get("value") is None:
            out[k] = assumed(d, q)
            continue
        value, ok = _coerce(item["value"], allowed)
        if not ok:
            a = assumed(d, q, reason=f"The AI gave {item['value']!r}, which is not an allowed answer")
            a.flags.append("invalid AI value")
            out[k] = a
            continue
        conf = item.get("confidence") if item.get("confidence") in ("H", "M", "L") else "L"
        a = Answer(value=value, basis="stated" if item.get("basis") == "stated" else "assumed",
                   reason=str(item.get("reason") or "")[:300], confidence=conf,
                   quotes=_quotes(item.get("quotes")), question=q)
        if a.basis == "assumed":
            upd: dict[str, Any] = {"confidence": "L"}
            if allowed is None:      # free text: an unquoted guess is never stored as the value
                upd.update(value=d, reason=f"AI suggestion: {value}"[:300])
            elif value != d:
                upd.update(value=d, reason=f"AI suggested {value!r} without a quote; framework default used")
            a = a.model_copy(update=upd)
        out[k] = a
    return out


def _default(path: str, s: Settings) -> Any:
    parts = path.split(".")
    fam = parts[0] if parts[0] != "steps" else parts[2]
    if fam in ("readiness_defaults", "readiness"):
        return s.defaults.readiness
    if fam == "dials":
        return s.defaults.rule_clarity if parts[-1] == "rule_clarity" else s.defaults.dial
    return {"r_pass1": "Unknown", "r_pass2": "Unknown", "tier1": "N", "agent_test": "N",
            "doc_gate": "N", "accountability": "N", "r_move": "Remove"}.get(fam)


def _set_answer(run: FrameworkRun, path: str, a: Answer) -> None:
    parts = path.split(".")
    if parts[0] != "steps":
        getattr(run, parts[0])[parts[1]] = a
    elif len(parts) == 4:
        getattr(run.steps[parts[1]], parts[2])[parts[3]] = a
    else:
        setattr(run.steps[parts[1]], parts[2], a)


def apply_verifier(run: FrameworkRun, findings: list[dict], s: Settings | None = None) -> FrameworkRun:
    """Downgrade only: "downgrade" -> framework default, assumed (old value kept in history);
    "lower_confidence" -> H to M, M to L. Unknown actions/paths, answered and derived answers are ignored."""
    s = s or load_settings()
    run = run.model_copy(deep=True)
    for f in findings if isinstance(findings, list) else []:
        if not isinstance(f, dict) or f.get("action") not in ("downgrade", "lower_confidence"):
            continue
        path, reason = str(f.get("path") or ""), str(f.get("reason") or "")
        try:
            old = get_answer(run, path)
        except KeyError:
            continue
        if old.basis == "answered" or old.derived:
            continue
        flags = [x for x in old.flags if not x.startswith(_CAP_PREFIX)]
        if f["action"] == "lower_confidence":
            new = old.model_copy(update={"confidence": "M" if old.confidence == "H" else "L",
                                         "flags": flags + [f"verifier: {reason}"[:200]]})
        elif old.basis == "assumed":
            continue
        else:
            new = Answer(value=_default(path, s), basis="assumed", reason=f"Verifier: {reason}"[:300],
                         confidence="L", quotes=old.quotes, question=old.question,
                         flags=flags + ["verifier downgrade"],
                         history=old.history + [{"value": old.value, "basis": old.basis,
                                                 "confidence": old.confidence, "reason": old.reason,
                                                 "by": "verifier"}])
        _set_answer(run, path, new)
    return run

# --------------------------------------------------------------------------- Stage 1 evidence


def _names(model: ProcessModel) -> dict[str, str]:
    out = {a.id: a.name for a in model.activities}
    out.update({d.id: d.question for d in model.decisions})
    return out


def _section(title: str, lines: list[str]) -> str:
    return f"{title}\n" + "\n".join(lines) if lines else ""


def stage1_context(model: ProcessModel, agent_run: AgentRun | None) -> str:
    """The Stage 1 evidence block every AI prompt gets after the map (spec: "What Stage 1 feeds in")."""
    names = _names(model)
    roles = {r.id: r.name for r in model.roles}

    def where(nid: str | None) -> str:
        return f"{nid} {names.get(nid, '')}".strip() if nid else "no step"

    def refs(rs) -> list[str]:
        return [f'    "{r.quote}" ({r.file} @ {r.location})' for r in rs if r.quote]

    pains = []
    for p in model.pain_points:
        pains.append(f"- {p.id} at {where(p.node_id)} [{p.category}, frequency {p.frequency or 'not stated'}]: "
                     f"{p.description}")
        pains += refs(p.source_refs)
        if p.category in _HINTS:
            pains.append(f"    hint: {_HINTS[p.category]}")
    parts = [_section("PAIN POINTS", pains),
             _section("OPEN QUESTIONS", [f"- {q}" for q in model.open_questions])]
    if agent_run is not None:
        dis = []
        for d in agent_run.discrepancies:
            dis.append(f"- {d.id} [{d.type}, {d.severity}] {d.subject}")
            dis += [f"    {f}: {says}" for f, says in d.per_source.items()]
        for r in agent_run.reconciliation:
            if r.kind in ("conflict", "single-source"):
                dis.append(f"- {r.kind} (steps {', '.join(r.node_ids) or '-'}): {r.subject}: {r.detail} "
                           f"Resolution: {r.resolution}")
        parts.append(_section("SOURCE DISAGREEMENTS", dis))
        parts.append(_section("REJECTED VERIFIER FINDINGS", [
            f"- {f.id} [{f.issue}] steps {', '.join(f.node_ids) or '-'}: {f.description} Reviser: {f.reviser_note}"
            for f in agent_run.findings if not f.applied]))
    parts.append(_section("HANDOFFS", [
        f"- at {where(h.at_node_id)}: {roles.get(h.from_role_id, h.from_role_id)} -> "
        f"{roles.get(h.to_role_id, h.to_role_id)}. {h.description}" for h in model.handoffs]))
    parts.append(_section("DEPENDENCIES", [
        f"- {where(d.node_id)} depends on {d.depends_on} ({d.type})" for d in model.dependencies]))

    cov = source_coverage(model)
    nodes = {a.id: a for a in model.activities}
    nodes.update({d.id: d for d in model.decisions})
    detail = []
    for sid in graph_facts(model).steps:
        n = nodes[sid]
        bits = [f"- {sid} {names[sid]} (lane {roles.get(n.role_id, n.role_id)}, {cov.get(sid, 0)} source(s))"]
        tools = getattr(n, "tools", [])
        if tools:
            bits.append(f"    tools: {', '.join(t.text for t in tags_for(tools))}")
        if n.human_minutes_per_cycle is not None:
            rng = f" (range {n.human_minutes_range})" if n.human_minutes_range else ""
            bits.append(f"    minutes per cycle: {n.human_minutes_per_cycle:g}{rng}")
        for label, v in (("purpose", n.purpose), ("control_now", n.control_now),
                         ("judgment_reason_stated", n.judgment_reason_stated),
                         ("step_type", getattr(n, "step_type", None)), ("notes", getattr(n, "notes", "")),
                         ("description", getattr(n, "description", ""))):
            if v not in (None, ""):
                bits.append(f"    {label}: {v}")
        if getattr(n, "assumptions", None):
            bits.append(f"    assumptions: {'; '.join(n.assumptions)}")
        detail += bits
    parts.append(_section("STEP DETAIL", detail))
    return "\n\n".join(p for p in parts if p)

# --------------------------------------------------------------------------- prompts and calls


def _system(agent: str, role: str) -> str:
    return (f"You are the {agent} step in CGS Advisors' Process Improvement Framework. {role}\n"
            "Ground rules:\n"
            "- Answer the questions only. Never decide calls, tiers or scores, and never apply thresholds: "
            "code does that from your answers.\n"
            "- Give a quote (file, location, quote of at most 15 words) for every \"stated\" answer, taken from "
            "the sources or the Stage 1 evidence. No quote means the answer is not stated.\n"
            "- When the materials don't say, answer \"Unknown\" (or null) with basis \"assumed\". Never guess.\n"
            "- Use roles, never personal names. Keep reasons to one short line.\n"
            "- Return ONLY JSON.\n"
            'Answer format: {"value": ..., "basis": "stated"|"assumed", "reason": str, '
            '"confidence": "H"|"M"|"L", "quotes": [{"file": str, "location": str, "quote": str}]}')


def _questions(qs: dict[str, str]) -> str:
    return "\n".join(f"  {k}: {q.format(s='the step')}" for k, q in qs.items())


def _loads(text: str) -> dict:
    obj = json.loads(_strip_fences(text))
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def _json_with_repair(system: str, user: str, model: str = MODEL) -> dict:
    """Call and parse; retry once with the error if the JSON is invalid. Truncated propagates."""
    try:
        return _loads(_call(system, user, model=model))
    except ValueError as e:
        retry = user + f"\n\nYour previous output was not valid JSON ({str(e)[:300]}). Return only the corrected JSON."
    return _loads(_call(system, retry, model=model))


class _Ctx:
    def __init__(self, model: ProcessModel, parsed: list[ParsedFile], agent_run: AgentRun | None,
                 facts: GraphFacts):
        self.model, self.facts = model, facts
        roles = {r.id: r.name for r in model.roles}
        names = _names(model)
        lines = [f"Process: {model.process.name}. {model.process.summary}".strip(),
                 "Lanes: " + "; ".join(f"{r.id} {r.name}" for r in model.roles)]
        for sid in facts.steps:
            nxt = ", ".join(sorted(facts.succ.get(sid, ()))) or "-"
            lines.append(f"{sid} | {names[sid]} | lane {roles.get(facts.actor[sid], facts.actor[sid])} | next: {nxt}")
        self.map_text = "\n".join(lines)
        self.evidence = stage1_context(model, agent_run)
        self.sources = to_prompt_text(parsed) if parsed else "(no source files; use the Stage 1 evidence)"

    def user(self, marker: str, task: str, ids: list[str] | None) -> str:
        scope = f"\nSTEPS TO ANSWER: {', '.join(ids)}\n" if ids is not None else ""
        return (f"[STEP:{marker}]\n{task}\n{scope}\nPROCESS MAP\n{self.map_text}\n\n"
                f"STAGE 1 EVIDENCE\n{self.evidence}\n\nSOURCES\n{self.sources}\n\nReturn the JSON now.")

    def ask(self, marker: str, system: str, task: str, ids: list[str] | None = None,
            model: str = MODEL) -> dict:
        """The whole process in one call; split by lane only when the output is truncated."""
        try:
            return _json_with_repair(system, self.user(marker, task, ids), model)
        except Truncated:
            lanes: dict[str, list[str]] = {}
            for sid in ids or []:
                lanes.setdefault(self.facts.actor[sid], []).append(sid)
            if len(lanes) < 2:
                raise
            merged: dict = {}
            for lane_ids in lanes.values():
                for k, v in _json_with_repair(system, self.user(marker, task, lane_ids), model).items():
                    if isinstance(v, dict) and isinstance(merged.get(k), dict):
                        merged[k].update(v)
                    else:
                        merged.setdefault(k, v)
            return merged


def _step_items(out: dict, run: FrameworkRun, key: str = "steps"):
    raw = out.get(key)
    for sid, item in (raw.items() if isinstance(raw, dict) else []):
        if sid in run.steps and isinstance(item, dict):
            yield sid, run.steps[sid], item


def _overrides(item: dict, keys: list[str], allowed, defaults, questions) -> dict[str, Answer]:
    """Answers that change an earlier one: only keys the AI gave, and only with a quote."""
    present = [k for k in keys if k in item]
    return {k: a for k, a in parse_answers(item, present, allowed, defaults, questions).items()
            if a.basis == "stated"}


def _qs(answers: dict[str, Answer], keys: list[str]) -> dict[str, str]:
    return {k: answers[k].question if k in answers else k for k in keys}

# --------------------------------------------------------------------------- AI steps
# Each takes (run copy, ctx, s) and returns (output JSON, run, summary).


def _gate_step(run, c, s):
    out = c.ask("gate", _system("Documentation gate", "You judge two documentation checks of the map."),
                "Answer Part 0 checks 3 and 8 (Y / Partly / N). The swimlane plus each step's description and "
                "notes count as the SOP: how-to detail = Y, only a name = Partly.\n"
                f"{_questions({k: _GATE_Q[k] for k in ('3', '8')})}\n"
                'Return {"answers": {"3": Answer, "8": Answer}}')
    run.doc_gate.update(parse_answers(out.get("answers"), ["3", "8"], YPN, {"3": "N", "8": "N"}, _GATE_Q))
    return out, run, f"Check 3 = {run.doc_gate['3'].value}, check 8 = {run.doc_gate['8'].value}"


def _context_step(run, c, s):
    out = c.ask("context", _system("Toil Task Assessor", "You read the business context and the step worksheet."),
                "Part 1 context. Answer:\n" + _questions(_CONTEXT_Q) +
                "\nMap checks (owner questions, not scored), types: alternatives_as_sequence, note_is_hidden_step, "
                "duplicate_gate, sop_map_mismatch, dead_end. Use open questions, source disagreements and "
                "rejected verifier findings.\nWorksheet per step:\n" + _questions(_WORKSHEET_Q) +
                f" (type_today is one of {', '.join(TYPE_TODAY)})\n"
                'Return {"context": {key: Answer}, "map_checks": [{"type", "step_ids", "question"}], '
                '"worksheet": {step_id: {key: Answer}}}', list(c.facts.steps))
    run.context = parse_answers(out.get("context"), list(_CONTEXT_Q), None, {}, _CONTEXT_Q)
    node_ids = {a.id for a in c.model.activities} | {d.id for d in c.model.decisions}
    run.map_checks = []
    for raw in out.get("map_checks") or []:
        try:
            mc = MapCheck(**raw)
        except (TypeError, ValidationError):
            continue
        mc.step_ids = [i for i in mc.step_ids if i in node_ids]
        if mc.step_ids:
            run.map_checks.append(mc)
    filled = 0
    for sid, st, item in _step_items(out, run, "worksheet"):
        for k in [k for k in st.worksheet if k in item and not st.worksheet[k].derived]:
            st.worksheet.update(parse_answers(item, [k], TYPE_TODAY if k == "type_today" else None,
                                              {}, _qs(st.worksheet, [k])))
            filled += 1
    stated = sum(a.basis == "stated" for a in run.context.values())
    return out, run, f"{stated}/{len(run.context)} context answers stated; {len(run.map_checks)} map checks; {filled} worksheet fields"


def _single_qs(c: _Ctx, sid: str) -> dict[str, str]:
    s = f'"{_names(c.model).get(sid, sid)}"'
    return {"r_move": f"R4: should {s} be moved (needed, but sits where it causes the wait) or removed?",
            "accountability": f"Is {s} an accountability step (a person checks, approves or owns the result)?",
            "combine_with": f"Which adjacent step could {s} merge with (step id)?"}


def _part2_step(run, c, s):
    out = c.ask("part2", _system("Step Removal", "You judge whether each step should exist, from the map as written."),
                "Part 2, pass 1. For every step answer R1-R7 (Y / N / Unknown):\n" + _questions(_R_Q) +
                "\nAlso, where relevant: r_move (Move / Remove, when R4 is Y), accountability (Y / N; purpose = audit "
                "is a hint), combine_with (a step id, when R3 or R6 is Y). Pain-point hints are evidence, not rules.\n"
                'Return {"steps": {step_id: {"R1": Answer, ..., "r_move": Answer, "accountability": Answer, '
                '"combine_with": Answer}}}', list(c.facts.steps))
    items = {sid: item for sid, _, item in _step_items(out, run)}
    for sid, st in run.steps.items():
        item = items.get(sid, {})
        st.r_pass1 = parse_answers(item, R_KEYS, YNU, {k: "Unknown" for k in R_KEYS}, _qs(st.r_pass1, R_KEYS))
        qs = _single_qs(c, sid)
        for k, allowed, d in (("r_move", R_MOVE, "Remove"), ("accountability", YN, "N"),
                              ("combine_with", tuple(i for i in c.facts.steps if i != sid), None)):
            if k in item:
                setattr(st, k, parse_answers(item, [k], allowed, {k: d}, qs)[k])
    yes = sum(a.value == "Y" for st in run.steps.values() for a in st.r_pass1.values())
    return out, run, f"{len(items)} steps answered, {yes} Y answers; code makes the calls"


def _r_changes(out, run) -> int:
    n = 0
    for sid, st, item in _step_items(out, run):
        ch = _overrides(item, R_KEYS, YNU, {k: "Unknown" for k in R_KEYS}, _qs(st.r_pass1, R_KEYS))
        st.r_pass2.update(ch)
        n += len(ch)
    return n


def _calls(run) -> str:
    return "; ".join(f"{sid}={st.call}" for sid, st in run.steps.items())


def _known_context(run: FrameworkRun) -> dict[str, Any]:
    """Context values backed by a quote or a person; assumptions never reach later prompts as fact."""
    return {k: a.value for k, a in run.context.items()
            if a.value is not None and a.basis in ("stated", "answered")}


def _part2_context_step(run, c, s):
    ctx = "; ".join(f"{k}: {v}" for k, v in _known_context(run).items())
    out = c.ask("part2_context", _system("Step Removal", "You revisit R1-R7 in the light of the business context."),
                f"Part 2, pass 2 (context). Business context: {ctx or 'none stated'}.\nCurrent calls (code's): "
                f"{_calls(run)}.\nReturn ONLY the R answers the business context changes, with quotes.\n"
                + _questions(_R_Q) + '\nReturn {"steps": {step_id: {"R<n>": Answer}}}', list(c.facts.steps))
    return out, run, f"{_r_changes(out, run)} answers changed by context"


def _tiers_step(run, c, s):
    ids = [sid for sid in c.facts.steps if run.steps[sid].call != "Remove"]
    qs = {**_D_Q, **_DIAL_Q, **_A_Q}
    out = c.ask("tiers", _system("Process vs Workflow Disambiguation", "You rate each step for the tiers."),
                "Part 3. For each step: D1-D3 (Y / Partly / N), the six dials (1-5), A1-A4 (Y / N). Hints: "
                "missing-rule pain points and judgment_reason_stated inform Rule clarity; step_type informs Judgment; "
                "tools and dependencies inform D1/D3.\n" + _questions(qs) +
                '\nReturn {"steps": {step_id: {key: Answer}}}', ids)
    n = 0
    for sid, st, item in _step_items(out, run):
        if sid not in ids:
            continue
        n += 1
        st.tier1 = parse_answers(item, D_KEYS, YPN, {k: "N" for k in D_KEYS}, _qs(st.tier1, D_KEYS))
        st.dials = parse_answers(item, DIAL_KEYS, SCORE, {k: _default(f"steps.x.dials.{k}", s) for k in DIAL_KEYS},
                                 _qs(st.dials, DIAL_KEYS))
        st.agent_test = parse_answers(item, A_KEYS, YN, {k: "N" for k in A_KEYS}, _qs(st.agent_test, A_KEYS))
    return out, run, f"{n} of {len(ids)} kept steps rated; code applies the tier rules"


def _loop_step(run, c, s):
    tiers = "; ".join(f"{sid}: {'standardize first, ' if st.tier.standardize_first else ''}{st.tier.tier}"
                      for sid, st in run.steps.items() if st.tier)
    out = c.ask("loop", _system("Step Removal", "You do the loop pass after the tiers."),
                f"Tier results (code's): {tiers or 'none'}.\nCurrent calls: {_calls(run)}.\n"
                "1) Which R1-R7 answers change now that steps are standardized or agentified? Changed answers only.\n"
                "2) Which steps does an Agent step make unnecessary? Map step id -> the Agent step's id.\n"
                + _questions(_R_Q) +
                '\nReturn {"steps": {step_id: {"R<n>": Answer}}, "made_unnecessary_by": {step_id: agent_step_id}}',
                list(c.facts.steps))
    n = _r_changes(out, run)
    groups = 0
    mu = out.get("made_unnecessary_by")
    for sid, by in (mu.items() if isinstance(mu, dict) else []):
        agent = run.steps.get(by) if isinstance(by, str) else None
        if sid in run.steps and by != sid and agent and agent.tier and agent.tier.tier == "Agent":
            run.steps[sid].ai_made_unnecessary_by = by
            groups += 1
    return out, run, f"{n} answers changed; {groups} steps made unnecessary by an Agent step"


def _readiness_step(run, c, s):
    out = c.ask("readiness", _system("Readiness", "You gather evidence for the five readiness tests."),
                "Part 4 (1-5 each). Process-level answers, plus per-step overrides only where a step differs.\n"
                + _questions(_READINESS_Q) +
                '\nReturn {"defaults": {key: Answer}, "steps": {step_id: {key: Answer}}}', list(c.facts.steps))
    d = {k: s.defaults.readiness for k in READINESS_KEYS}
    run.readiness_defaults = parse_answers(out.get("defaults"), READINESS_KEYS, SCORE, d, _READINESS_Q)
    n = 0
    for _, st, item in _step_items(out, run):
        ov = _overrides(item, READINESS_KEYS, SCORE, d, _READINESS_Q)
        st.readiness.update(ov)
        n += len(ov)
    stated = sum(a.basis == "stated" for a in run.readiness_defaults.values())
    return out, run, f"{stated}/5 process tests stated; {n} per-step overrides"


def _stated_answers(run: FrameworkRun) -> list[tuple[str, Answer]]:
    out = [(f"{fam}.{k}", a) for fam in ("doc_gate", "context", "readiness_defaults")
           for k, a in getattr(run, fam).items()]
    out += [(f"steps.{sid}.{suf}", a) for sid, st in run.steps.items() for suf, a in _step_answers(st)]
    return [(p, a) for p, a in out if a.basis == "stated" and not a.derived]


def _verify_step(run, c, s):
    items = [{"path": p, "value": a.value, "reason": a.reason, "quotes": [q.model_dump() for q in a.quotes]}
             for p, a in _stated_answers(run)]
    if not items:
        return {"findings": []}, run, "No stated answers to check"
    out = c.ask("verify", _system("Verifier (independent)", "You check every stated answer against its quote."),
                "For each answer below, does its quote, in the sources or the Stage 1 evidence, support the value? "
                "You can only downgrade: action \"downgrade\" (the quote does not support it; the framework default "
                "is used) or \"lower_confidence\" (supported, but weakly). Never raise a score or suggest a better "
                "value. Report nothing for sound answers.\n"
                f"ANSWERS:\n{json.dumps(items, default=str)}\n"
                'Return {"findings": [{"path": str, "action": "downgrade"|"lower_confidence", "reason": str}]}',
                model=REVIEW_MODEL)
    findings = out.get("findings") if isinstance(out.get("findings"), list) else []
    run = apply_verifier(run, findings, s)
    return out, run, f"{len(items)} stated answers checked; {len(findings)} findings (downgrades only)"


def _valid_chain(raw, default: list[ChainStep], roles: set[str]) -> list[ChainStep] | None:
    """The AI's chain, if valid: known types, keeps every type code chose, adds no Agent code didn't pick."""
    try:
        chain = [ChainStep(**x) for x in raw]
    except (TypeError, ValidationError):
        return None
    if not chain_compatible(chain, default):
        return None
    for x in chain:
        if x.role_id not in roles:
            x.role_id = None
    return chain


def _specs_step(run, c, s):
    ids = [sid for sid in c.facts.steps if run.steps[sid].recommendation and run.steps[sid].recommendation.chain]
    if not ids:
        return {"steps": {}}, run, "No kept steps need a future-state chain"
    plan = "\n".join(f"  {sid}: {run.steps[sid].recommendation.action}; types: "
                     f"{' -> '.join(x.type for x in run.steps[sid].recommendation.chain)}" for sid in ids)
    out = c.ask("specs", _system("Workflow Documentation & Specification",
                                 "You write the future-state chain and the spec for each new step."),
                "For each step, write its future-state chain: ordered new steps, each with a Verb + Noun name, a type "
                "(Rules / Agent / Human approval / Human task) and a role_id. Keep every type code chose; do not add "
                "an Agent. Then a spec per new step:\n- Rules: owner, logic, test cases, run record.\n- Agent: goal, "
                "systems read and written, never-dos, handoff to a person, logs, error budget, failure modes, down "
                "behaviour.\n- Human approval: authority, what they see, what they decide, speed, record.\n- Human "
                "task: what they do, skills, why it stays human.\nCode's results:\n" + plan +
                '\nReturn {"steps": {step_id: {"chain": [{"name", "type", "role_id"}], "specs": [{"name", "type", ...}]}}}',
                ids)
    roles = {r.id for r in c.model.roles}
    kept = 0
    for sid, st, item in _step_items(out, run):
        if sid not in ids:
            continue
        chain = _valid_chain(item.get("chain") or [], st.recommendation.chain, roles)
        if chain:
            st.ai_chain, st.recommendation.chain = chain, [x.model_copy() for x in chain]
            kept += 1
        types = {x.type for x in st.recommendation.chain}
        specs = item.get("specs")
        st.specs = [x for x in specs if isinstance(x, dict) and x.get("type") in types] if isinstance(specs, list) else []
    return out, run, f"{kept} of {len(ids)} chains taken from the AI (code default kept otherwise)"


def _summary_step(run, c, s):
    names = _names(c.model)
    results = {
        "process": c.model.process.name,
        "context": _known_context(run),
        "doc_confidence": f"{run.gate_score:g}/8 ({run.gate_band})",
        "redesign_potential": run.redesign_potential, "posture": run.posture, "waves": run.waves,
        "map_checks": [m.question for m in run.map_checks], "follow_ups": len(run.follow_ups),
        "steps": [{"id": sid, "name": names.get(sid, sid), "call": st.call,
                   "action": st.recommendation.action if st.recommendation else None,
                   "wave": st.recommendation.wave if st.recommendation else None,
                   "readiness": st.readiness_min} for sid, st in run.steps.items()]}
    system = _system("Summary", "You write the summary from computed results only.")
    out = _json_with_repair(system, "[STEP:summary]\nWrite summary_text (one context paragraph, at most 120 words) "
                            "and first_actions (exactly 3 short actions) from these computed results only. Add no "
                            "numbers, calls or tiers that are not in them.\nRESULTS:\n"
                            f"{json.dumps(results, default=str)}\n"
                            'Return {"summary_text": str, "first_actions": [str, str, str]}')
    run.summary_text = str(out.get("summary_text") or "")
    acts = out.get("first_actions")
    run.first_actions = [str(a) for a in acts if str(a).strip()][:3] if isinstance(acts, list) else []
    return out, run, f"Summary written; {len(run.first_actions)} first actions"

# --------------------------------------------------------------------------- pipeline


def framework_hash(model: ProcessModel, parsed: list[ParsedFile], s: Settings | None = None) -> str:
    """Map + source chunks (file, location, text) + the number of loop passes (it changes which AI
    steps run). Thresholds and defaults are left out: apply_rules applies the current settings to a
    cached or replayed run, so editing settings.toml keeps the cache and the demo replay usable."""
    h = hashlib.sha256(model.model_dump_json().encode())
    for pf in parsed:
        for ch in pf.chunks:
            for part in (pf.name, ch.location, ch.text):
                h.update(part.encode() + b"\0")
            h.update(b"\1")
    h.update(b"\2loop.extra_passes=%d" % (s or load_settings()).loop.extra_passes)
    return h.hexdigest()[:16]


def _saved(key: str) -> FrameworkRun | None:
    """A cached run, or the demo replay when its input hash matches. Unreadable files are a miss."""
    cache = CACHE_DIR / f"framework_{key}.json"
    for path, replay in ((cache, False), (DEMO_RUN, True)):
        try:
            if not path.exists():
                continue
            run = FrameworkRun.model_validate_json(path.read_text())
        except (ValidationError, ValueError, OSError):
            continue
        if not replay:
            return run
        if run.artifacts.get("input_hash") == key:
            run.mode = "replay"
            return run
    return None


def run_framework(model: ProcessModel, parsed: list[ParsedFile], *, agent_run: AgentRun | None = None,
                  map_reviewed: bool, success_end: str | None = None, force_live: bool = False,
                  progress: Callable[[AgentStep], None] | None = None, s: Settings | None = None,
                  names: list[str] | None = None, prior: FrameworkRun | None = None) -> FrameworkRun:
    """Run (or load) the framework. Every answer a person gave on `prior` is kept: it is put on the
    new run before any AI step (so prompts see it), put back after every AI step (so AI output for
    that path is ignored) and kept through every code pass. A cached or replayed run gets them too."""
    progress = progress or (lambda st: None)
    s = s or load_settings()
    key = framework_hash(model, parsed, s)
    carried = answered_answers(prior) if prior is not None else {}
    if not force_live:
        run = _saved(key)
        if run is not None:
            for st in run.trace:
                progress(st)
            run.map_reviewed = map_reviewed
            run.success_end = success_end or run.success_end
            carry_answers(run, carried)
            return apply_rules(run, model, s, names)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(NO_KEY)

    trace: list[AgentStep] = []
    artifacts: dict[str, Any] = {"input_hash": key}

    def record(name, agent, kind, summary, out=None, model_name=None, status="ok", t0=None):
        st = AgentStep(id=f"F{len(trace) + 1}", name=name, agent=agent, kind=kind, summary=summary,
                       model=model_name, status=status, artifact=None if out is None else f"F{len(trace) + 1}",
                       duration_s=None if t0 is None else round(time.time() - t0, 1))
        if out is not None:
            artifacts[st.id] = out
        trace.append(st)
        progress(st)

    run = blank_run(model, "live", s)
    carry_answers(run, carried)
    run.map_reviewed = map_reviewed
    run.success_end = success_end or run.success_end
    facts = graph_facts(model, run.success_end)
    c = _Ctx(model, parsed, agent_run, facts)
    record("Load", "Orchestrator", "code",
           f"{len(facts.steps)} steps, {len(parsed)} source files, success end {facts.success_end}; "
           f"{len(facts.main_path)} steps on the main path")

    failed = False

    def ai(name, agent, fn, rules=False, model_name=MODEL):
        nonlocal run, failed
        t0 = time.time()
        try:
            out, new, summary = fn(run.model_copy(deep=True), c, s)
            run = new
            record(name, agent, "llm", summary, out, model_name, t0=t0)
        except Exception as e:  # noqa: BLE001 - a run never stops; answers stay at the defaults
            failed = True
            record(name, agent, "llm", f"Failed ({str(e)[:120]}); answers stay at the framework defaults.",
                   model_name=model_name, status="warning", t0=t0)
        carry_answers(run, carried)      # a person's answer always beats the AI's
        if rules:
            run = apply_rules(run, model, s, names)

    ai("Doc gate", "Documentation gate", _gate_step, rules=True)
    ai("Context reader", "Toil Task Assessor", _context_step)
    scoring = [("Step Removal, pass 1", "Step Removal", _part2_step),
               ("Step Removal, pass 2 (context)", "Step Removal", _part2_context_step),
               ("Disambiguation", "Process vs Workflow Disambiguation", _tiers_step)]
    scoring += [("Step Removal, loop pass", "Step Removal", _loop_step)] * s.loop.extra_passes
    for name, agent, fn in scoring + [("Readiness", "Readiness", _readiness_step)]:
        if run.gate_band == "Low":
            record(name, agent, "code", "Skipped: documentation confidence is Low (document first).", status="warning")
        else:
            ai(name, agent, fn, rules=fn is not _readiness_step)
    ai("Verifier", "Verifier (independent)", _verify_step, model_name=REVIEW_MODEL)
    run = apply_rules(run, model, s, names)
    record("Recommend", "Orchestrator", "code",
           f"Posture: {'; '.join(run.posture)}. Redesign potential {run.redesign_potential}. "
           f"{len(run.follow_ups)} follow-up questions.")
    ai("Workflow Documentation & Specification", "Workflow Documentation & Specification", _specs_step)
    ai("Summary", "Summary", _summary_step)

    run.trace, run.artifacts, run.mode = trace, artifacts, "live"
    if not failed:   # a run with a failed AI step is never cached, so the next run retries it
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"framework_{key}.json").write_text(run.model_dump_json(indent=2))
    return run
