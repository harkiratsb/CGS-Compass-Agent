"""
Multi-agent extraction (the agentic module inside the Stage 1 hybrid flow).

                         ┌─ Extract source A ─┐
    Plan (code) ─────────┼─ Extract source B ─┼─▶ Compare sources ─▶ Reconcile ─┐
                         └─ Verifier: blind checklist from sources ─────────────┤
                                                                                 ▼
                          ┌──────────── up to MAX_REVIEW_ROUNDS ─────────────────┐
                          │ Verifier checks model vs checklist + sources ─▶ findings
                          │ Reviser applies (or rejects with reason) ─▶ revised model
                          └── stop when the verifier finds no high/medium issues ┘
                                                                                 ▼
                                                                       Validate (code)

Roles and models
  Extractor / Comparer / Reconciler / Reviser : CGS_MODEL         (default claude-sonnet-5-5)
  Verifier                                    : CGS_REVIEW_MODEL  (default claude-opus-5-5)
  The verifier never sees the extractor's or reconciler's reasoning, builds its checklist
  BEFORE seeing the model, and never edits the model itself.

Every step records its output JSON (AgentStep.artifact -> AgentRun.artifact()) for the UI.

Modes
  live   : ANTHROPIC_API_KEY set and "run live" ticked (or no cache). Cached in .cache/agent_<hash>.json.
  replay : demo transcripts with no cached live run -> demo_data/agent_run_sales_process.json.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from pydantic import ValidationError

from ingest.extract import MODEL, SYSTEM_PROMPT, _demo_hash, _files_hash, _strip_fences
from ingest.parse import ParsedFile, to_prompt_text
from schema import (AgentRun, AgentStep, Alignment, ChecklistItem, Discrepancy, ProcessModel,
                    ReconItem, ReviewFinding)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
DEMO_RUN = ROOT / "demo_data" / "agent_run_sales_process.json"
REVIEW_MODEL = os.environ.get("CGS_REVIEW_MODEL", "claude-opus-5-5")
MAX_REVIEW_ROUNDS = 2

Progress = Callable[[AgentStep], None]

COMPACT = ("Keep the output compact (it must fit in one response): descriptions <= 20 words, quotes <= 15 words, "
           "at most one source_ref per source file per item.")

# --------------------------------------------------------------------------- prompts
COMPARE_PROMPT = f"""You are the source-comparison step of CGS Advisors' process-discovery agent.
You receive CURRENT-STATE process models, each extracted independently from ONE source describing the same process.
Do NOT merge them. Line them up and report where they differ.

1. alignment: one row per distinct step/decision across all sources. per_source maps each source file name to the
   matching node id in that source's model, or null if the source does not have it.
2. discrepancies: every place the sources disagree or one is silent:
   - missing_in_source: a step/decision/branch present in one source but not another
   - order: sequence differs     - role: different owner/lane     - logic: different branching or loop target
   - terminology: same thing named differently (incl. personal names vs roles)
   - detail: same step, materially different detail
   For each, state what EACH source says (per_source: file -> short statement) and cite quotes.
   Severity: high = changes the flow; medium = changes ownership/order; low = wording/detail.
{COMPACT}

Return ONLY JSON: {{"alignment": [{{"step": str, "kind": str, "per_source": {{file: id|null}}}}],
                   "discrepancies": [{{"id": "X1", "type": ..., "severity": ..., "subject": str,
                                       "per_source": {{file: str}}, "sources": [SourceRef]}}]}}
"""

RECONCILE_PROMPT = f"""You are the reconciliation step of CGS Advisors' process-discovery agent.
You receive per-source CURRENT-STATE process models and a DISCREPANCY REPORT comparing them.
Produce ONE merged ProcessModel and a reconciliation log. You must address EVERY discrepancy id.

- Same step described differently -> one activity, keeping source_refs from every source that supports it.
- Present in one source only -> keep it if the speakers treat it as real current practice ("single-source").
- Sources disagree -> follow the speakers' FINAL statements; log as "conflict" with both versions and your reasoning.
- Personal names -> roles (CGS standard), "renamed".  Aspirational ideas -> not current state, "excluded".
- Follow every modelling rule of the extraction step (Verb + Noun labels, decisions as questions, every branch
  mirrored in edges, start/end events, detail in `notes`). Keep IDs unique.
{COMPACT} 6-12 reconciliation items.

Return ONLY JSON: {{"model": <ProcessModel>, "reconciliation": [{{"kind": "merged"|"conflict"|"single-source"|"renamed"|"excluded",
  "subject": str, "detail": str, "resolution": str, "discrepancy_ids": [str], "node_ids": [str], "sources": [SourceRef]}}]}}
"""

CHECKLIST_PROMPT = f"""You are the independent Verifier in CGS Advisors' process-discovery tool.
Read the source materials and build your OWN checklist of everything a correct current-state process map must
contain: activities, decisions, loops, handoffs between roles, subprocesses and end points.
You have NOT seen anyone else's map, and you must not guess at one. Work only from the sources.
Use roles, not personal names. Exclude ideas for future automation. 20-40 items, ordered as the process runs.
{COMPACT}

Return ONLY JSON: {{"checklist": [{{"id": "C1", "kind": "activity"|"decision"|"loop"|"handoff"|"subprocess"|"end",
                   "description": str, "role": str, "evidence": [SourceRef]}}]}}
"""

VERIFY_PROMPT = f"""You are the independent Verifier in CGS Advisors' process-discovery tool.
You built the CHECKLIST below from the sources yourself, before seeing this model. Now check the MODEL:

- For each checklist item: is it represented correctly (right lane, right order, right branches)?
- Is anything in the model unsupported by the sources?
- CGS standards: roles not names, Verb + Noun task labels, decisions as questions with labelled branches,
  an end event on every path, detail in notes rather than labels.

Issue types: missing_step, missing_branch, wrong_role, wrong_order, unsupported, weak_evidence, standards.
Severity: high = flow wrong or incomplete; medium = ownership/order; low = cosmetic or evidence.
Report findings only. Do NOT rewrite the model. Do not invent problems; if it is sound, return none. At most 8 findings.
{COMPACT}

Return ONLY JSON: {{"coverage": "<n>/<total> checklist items correctly represented",
                   "findings": [{{"id": "F<n>", "severity": ..., "issue": ..., "description": str,
                                 "fix": "<suggested fix>", "checklist_ids": [str], "node_ids": [str], "evidence": [SourceRef]}}]}}
"""

REVISE_PROMPT = f"""You are the Reviser in CGS Advisors' process-discovery tool.
An independent verifier reviewed the process MODEL and reported FINDINGS. For each finding, decide:
apply it (make the change) or reject it (if the sources or CGS standards contradict it). Give a one-line reason either way.
Follow every modelling rule of the extraction step. Keep IDs stable.
{COMPACT}

Return ONLY JSON: {{"model": <full revised ProcessModel>,
                   "decisions": [{{"id": "F<n>", "applied": true|false, "reviser_note": str}}]}}
"""


# --------------------------------------------------------------------------- llm plumbing
class Truncated(Exception):
    """The model hit max_tokens before finishing its JSON."""


def _call(system: str, user: str, max_tokens: int = 32000, model: str = MODEL) -> str:
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(model=model, max_tokens=max_tokens, system=system,
                                messages=[{"role": "user", "content": user}]) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if msg.stop_reason == "max_tokens":
        raise Truncated(f"output truncated at {len(text):,} chars (max_tokens={max_tokens})")
    return text


CONCISE = ("\n\nIMPORTANT: your previous answer was too long and was cut off. Be more concise: descriptions <= 15 words, "
           "quotes <= 12 words, at most ONE source_ref per source file per item, omit empty fields and assumptions.")


def _schema_json() -> str:
    return json.dumps(ProcessModel.model_json_schema())


def _parse_model(raw_obj) -> ProcessModel:
    m = ProcessModel(**raw_obj)
    problems = m.validate_graph()
    if problems:
        raise ValueError("; ".join(problems[:12]))
    return m


def _json_with_repair(system: str, user: str, parse: Callable[[dict], object], model: str = MODEL):
    """Call, parse, and on failure retry once: shorter if truncated, with the errors if invalid."""
    try:
        return parse(json.loads(_strip_fences(_call(system, user, model=model))))
    except Truncated:
        retry = user + CONCISE
    except (json.JSONDecodeError, ValidationError, ValueError, KeyError, TypeError) as e:
        retry = user + f"\n\nYour previous output failed validation:\n{str(e)[:3000]}\nReturn the full corrected JSON."
    return parse(json.loads(_strip_fences(_call(system, retry, model=model))))


def _counts(m: ProcessModel) -> str:
    acts = len([a for a in m.activities if a.kind not in ("start", "end")])
    roles = len([r for r in m.roles if any(a.role_id == r.id for a in m.activities)])
    return f"{roles} roles · {acts} activities · {len(m.decisions)} decisions · {len(m.pain_points)} pain points"


# --------------------------------------------------------------------------- steps
def _extract_one(pf: ParsedFile, hint: str) -> ProcessModel:
    user = (f"Process being mapped: {hint or 'infer from materials'}\n"
            f"This is ONE source among several; extract only what THIS source says.\n\n"
            f"JSON schema (Pydantic):\n{_schema_json()}\n\nSOURCE MATERIALS:\n{to_prompt_text([pf])}\n\n"
            "Return the ProcessModel JSON now.")
    return _json_with_repair(SYSTEM_PROMPT, user, _parse_model)


def _checklist(source_text: str, hint: str) -> List[ChecklistItem]:
    user = f"Process: {hint}\n\nSOURCES:\n{source_text}\n\nReturn the checklist JSON now."
    return _json_with_repair(CHECKLIST_PROMPT, user,
                             lambda o: [ChecklistItem(**c) for c in o["checklist"]], model=REVIEW_MODEL)


def _compare(models: dict) -> Tuple[List[Alignment], List[Discrepancy]]:
    blocks = "\n\n".join(f"=== MODEL FROM SOURCE: {n} ===\n{m.model_dump_json()}" for n, m in models.items())
    return _json_with_repair(COMPARE_PROMPT, blocks + "\n\nReturn the comparison JSON now.",
                             lambda o: ([Alignment(**a) for a in o.get("alignment", [])],
                                        [Discrepancy(**d) for d in o.get("discrepancies", [])]))


def _reconcile(models: dict, discrepancies: List[Discrepancy], hint: str) -> Tuple[ProcessModel, List[ReconItem]]:
    blocks = "\n\n".join(f"=== MODEL FROM SOURCE: {n} ===\n{m.model_dump_json()}" for n, m in models.items())
    disc = json.dumps([d.model_dump() for d in discrepancies])
    user = (f"Process: {hint}\n\nProcessModel schema:\n{_schema_json()}\n\n{blocks}\n\n"
            f"=== DISCREPANCY REPORT ===\n{disc}\n\nReturn the merged JSON now.")
    return _json_with_repair(RECONCILE_PROMPT, user,
                             lambda o: (_parse_model(o["model"]), [ReconItem(**r) for r in o.get("reconciliation", [])]))


def _verify(model: ProcessModel, checklist: List[ChecklistItem], source_text: str,
            round_no: int) -> Tuple[str, List[ReviewFinding]]:
    user = (f"CHECKLIST (yours):\n{json.dumps([c.model_dump() for c in checklist])}\n\n"
            f"MODEL UNDER REVIEW:\n{model.model_dump_json()}\n\nSOURCES:\n{source_text}\n\nReturn the review JSON now.")

    def parse(o):
        fs = [ReviewFinding(round=round_no, applied=False,
                            **{k: v for k, v in f.items() if k not in ("round", "applied")})
              for f in o.get("findings", [])]
        return o.get("coverage", ""), fs
    return _json_with_repair(VERIFY_PROMPT, user, parse, model=REVIEW_MODEL)


def _revise(model: ProcessModel, findings: List[ReviewFinding], source_text: str) -> ProcessModel:
    user = (f"ProcessModel schema:\n{_schema_json()}\n\nMODEL:\n{model.model_dump_json()}\n\n"
            f"FINDINGS:\n{json.dumps([f.model_dump() for f in findings])}\n\nSOURCES:\n{source_text}\n\n"
            "Return the revision JSON now.")

    def parse(o):
        revised = _parse_model(o["model"])
        decisions = {d["id"]: d for d in o.get("decisions", [])}
        for f in findings:
            d = decisions.get(f.id, {})
            f.applied = bool(d.get("applied", False))
            f.reviser_note = d.get("reviser_note", "")
        return revised
    return _json_with_repair(REVISE_PROMPT, user, parse)


# --------------------------------------------------------------------------- orchestrator
def run_agent(parsed: List[ParsedFile], raw_files: List[Tuple[str, bytes]], hint: str = "",
              force_live: bool = False, progress: Optional[Progress] = None) -> AgentRun:
    progress = progress or (lambda s: None)
    key = _files_hash(raw_files)
    cache = CACHE_DIR / f"agent_{key}.json"

    if not force_live:
        for path in (cache, DEMO_RUN if key == _demo_hash() else None):
            if path and path.exists():
                run = AgentRun(**json.loads(path.read_text()))
                for s in run.steps:
                    progress(s)
                return run

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("No API key and no pre-computed run for these files. "
                           "Set ANTHROPIC_API_KEY in .env, or load the demo materials.")

    steps: List[AgentStep] = []

    def record(**kw) -> AgentStep:
        s = AgentStep(id=f"S{len(steps) + 1}", **kw)
        steps.append(s)
        progress(s)
        return s

    source_text = to_prompt_text(parsed)
    total_chunks = sum(len(p.chunks) for p in parsed)
    record(name="Plan", agent="Orchestrator", kind="code",
           summary=f"{len(parsed)} sources, {total_chunks} chunks. Extract each source and build the verifier's blind "
                   f"checklist in parallel → compare sources → reconcile → verify/revise (≤{MAX_REVIEW_ROUNDS} rounds) → validate.")

    # ---- parallel: per-source extraction + verifier's blind checklist
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(5, len(parsed) + 1)) as ex:
        ext = {pf.name: ex.submit(_extract_one, pf, hint) for pf in parsed}
        chk = ex.submit(_checklist, source_text, hint)
        per_source = {n: f.result() for n, f in ext.items()}
        try:
            checklist = chk.result()
            chk_err = None
        except Exception as e:  # noqa: BLE001
            checklist, chk_err = [], str(e)[:120]
    dt = round(time.time() - t0, 1)
    for n, m in per_source.items():
        record(name=f"Extract — {n}", agent="Interview & Transcript Synthesizer", kind="llm", model=MODEL,
               summary=_counts(m), artifact=f"per_source:{n}", duration_s=dt)
    record(name="Build blind checklist", agent="Verifier (independent)", kind="llm", model=REVIEW_MODEL,
           status="ok" if not chk_err else "warning", artifact="checklist", duration_s=dt,
           summary=(f"{len(checklist)} items built from the sources only — the verifier has not seen any model."
                    if not chk_err else f"Checklist failed ({chk_err}); verification will rely on the sources alone."))

    # ---- compare sources
    alignment: List[Alignment] = []
    discrepancies: List[Discrepancy] = []
    if len(per_source) > 1:
        t0 = time.time()
        try:
            alignment, discrepancies = _compare(per_source)
            sev = ", ".join(f"{sum(d.severity == s for d in discrepancies)} {s}" for s in ("high", "medium", "low"))
            gaps = sum(1 for a in alignment if any(v is None for v in a.per_source.values()))
            record(name="Compare sources", agent="Knowledge Ingestion", kind="llm", model=MODEL, artifact="compare",
                   summary=f"{len(alignment)} steps aligned; {gaps} appear in only some sources. "
                           f"{len(discrepancies)} discrepancies ({sev}).", duration_s=round(time.time() - t0, 1))
        except Exception as e:  # noqa: BLE001
            record(name="Compare sources", agent="Knowledge Ingestion", kind="llm", model=MODEL, status="warning",
                   summary=f"Comparison failed ({str(e)[:120]}); reconciling without a discrepancy report.",
                   duration_s=round(time.time() - t0, 1))

    # ---- reconcile
    recon: List[ReconItem] = []
    if len(per_source) > 1:
        t0 = time.time()
        try:
            draft, recon = _reconcile(per_source, discrepancies, hint)
            addressed = {i for r in recon for i in r.discrepancy_ids}
            unaddr = [d.id for d in discrepancies if d.id not in addressed]
            record(name="Reconcile sources", agent="Knowledge Ingestion", kind="llm", model=MODEL, artifact="reconcile",
                   status="ok" if not unaddr else "warning",
                   summary=f"{len(recon)} decisions logged; {len(addressed & {d.id for d in discrepancies})}/"
                           f"{len(discrepancies)} discrepancies resolved" +
                           (f" (unaddressed: {', '.join(unaddr)})" if unaddr else "") + f". Draft: {_counts(draft)}",
                   duration_s=round(time.time() - t0, 1))
        except Exception as e:  # noqa: BLE001
            name, draft = max(per_source.items(), key=lambda kv: len(kv[1].activities))
            record(name="Reconcile sources", agent="Knowledge Ingestion", kind="llm", model=MODEL, status="warning",
                   summary=f"Reconciliation failed ({str(e)[:120]}). Recovered: continuing from {name}; "
                           f"the verifier checks it against ALL sources.", duration_s=round(time.time() - t0, 1))
    else:
        draft = next(iter(per_source.values()))
        record(name="Reconcile sources", agent="Knowledge Ingestion", kind="code", summary="Single source — nothing to reconcile.")

    # ---- verify / revise loop
    current = draft
    findings: List[ReviewFinding] = []
    revisions: dict = {}
    for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
        t0 = time.time()
        try:
            coverage, f_round = _verify(current, checklist, source_text, rnd)
        except Exception as e:  # noqa: BLE001
            record(name=f"Verify — round {rnd}", agent="Verifier (independent)", kind="llm", model=REVIEW_MODEL,
                   status="warning", summary=f"Verification failed ({str(e)[:120]}). Keeping the current model.")
            break
        findings += f_round
        blocking = [f for f in f_round if f.severity in ("high", "medium")]
        sev = ", ".join(f"{sum(f.severity == s for f in f_round)} {s}" for s in ("high", "medium", "low"))
        record(name=f"Verify — round {rnd}", agent="Verifier (independent)", kind="llm", model=REVIEW_MODEL,
               artifact=f"verify:{rnd}", duration_s=round(time.time() - t0, 1),
               summary=(f"Checklist coverage {coverage}. " if coverage else "") +
                       f"{len(f_round)} findings ({sev})." + ("" if blocking else " No blocking issues."))
        if not f_round:
            break
        t0 = time.time()
        try:
            current = _revise(current, f_round, source_text)
            revisions[rnd] = current
            ap = sum(f.applied for f in f_round)
            record(name=f"Revise — round {rnd}", agent="Reviser", kind="llm", model=MODEL, artifact=f"revise:{rnd}",
                   summary=f"{ap} applied, {len(f_round) - ap} rejected with reasons.",
                   duration_s=round(time.time() - t0, 1))
        except Exception as e:  # noqa: BLE001
            record(name=f"Revise — round {rnd}", agent="Reviser", kind="llm", model=MODEL, status="warning",
                   summary=f"Revision failed ({str(e)[:120]}). Keeping the previous model.")
            break
        if not blocking:
            break

    # ---- validate
    problems = current.validate_graph()
    record(name="Validate", agent="Orchestrator", kind="code", artifact="final",
           status="ok" if not problems else "warning",
           summary="Graph valid: every branch and edge resolves to a known node." if not problems
           else "Integrity warnings: " + "; ".join(problems[:5]))

    run = AgentRun(mode="live", steps=steps, per_source=per_source, alignment=alignment,
                   discrepancies=discrepancies, draft=draft, reconciliation=recon, checklist=checklist,
                   findings=findings, revisions=revisions, final=current)
    CACHE_DIR.mkdir(exist_ok=True)
    cache.write_text(run.model_dump_json(indent=2))
    return run
