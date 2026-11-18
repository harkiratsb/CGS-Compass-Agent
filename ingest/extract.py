"""
Extraction agent: parsed chunks -> ProcessModel.

Two modes, chosen automatically:
  live   : ANTHROPIC_API_KEY set -> call Claude, validate JSON against schema, retry once on failure
  cached : no key, or a cache hit for this exact set of input files -> load from .cache / demo_data

Cache key = sha256 over sorted (filename, content) pairs, so the same uploads
always resolve to the same extraction. The shipped demo extraction is registered
under the hash of the two demo transcripts in demo_data/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import ValidationError

from ingest.parse import ParsedFile, to_prompt_text
from cgs_tools import prompt_block
from schema import ProcessModel

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
DEMO_DIR = ROOT / "demo_data"
MODEL = os.environ.get("CGS_MODEL", "claude-sonnet-5-5")

SYSTEM_PROMPT = """You are the Interview & Transcript Synthesizer agent in CGS Advisors' process-discovery tool.
You read raw process materials (workshop transcripts, walkthroughs, SOP documents) and produce a
structured CURRENT-STATE process model at the PROCESS level (who does what, in what order, with which
decisions and handoffs) — not click-level SOP detail.

Rules:
- Output ONLY a JSON object matching the schema. No prose, no markdown fences.
- Roles become swimlanes. Prefer role names over personal names (e.g. "Lead Owner" not "Quinn").
  Keep to 4-8 roles. Mark external parties (clients, leads, vendors) with is_external=true.
- Every activity and decision must have a role_id and an integer `order` giving rough left-to-right position.
- Model branching as `decisions` with `branches`, AND mirror every branch in `edges` (with the branch label).
  Every activity needs at least one incoming and one outgoing edge except start/end nodes.
- Include one start node (kind="start") and one or more end nodes (kind="end").
- Mark steps that are explicitly subprocesses or "its own process" as kind="subprocess".
- `step_type`: "deterministic" for rule-based, system or clerical steps that follow a fixed procedure;
  "judgment" for steps requiring human reasoning, negotiation, relationship or expertise; "unclear" otherwise.
- Activity `name` is a short Verb + Noun label (2-4 words, e.g. "Contact lead", "Prepare discussion document").
  Never put the role or system detail in the label; put it in `notes` (one short sentence shown as a callout).
- `tools`: software the step uses, following the CGS tool rules below.
- `assumptions`: unstated assumptions the speakers rely on.
- `pain_points`: friction, rework, delays, manual toil, unclear ownership, or missing rules that the
  speakers describe or complain about. Link each to the node where it occurs.
- `source_refs`: cite the file and location tag (e.g. "00:24:22" or "p.3") for every activity, decision
  and pain point, with a short quote (<= 25 words). When two files describe the same step, cite both.
- People appear only as roles, never by name, in EVERY field including quotes: replace a personal name inside a
  quote with the role in square brackets, e.g. "it is not [Lead Owner]'s area of expertise".
- Step sizing, for every activity (not start/end events) and every decision:
  - `purpose`: "intent" (a person sets the goal or direction), "execution" (doing the work itself) or
    "audit" (a person checks, reviews or approves a result).
  - `control_now`: how the step is done today. C0 manual work, C1 scripted automation, C2 AI-assisted insights,
    C3 agentic co-pilot, C4 bounded autonomy, C5 strategic operator. Most current-state steps are C0; use C2
    when a person uses an AI tool for part of the step (e.g. Gemini taking meeting notes).
  - `human_minutes_per_cycle`: rough human minutes the step takes each time the process runs (one lead /
    opportunity). Use `human_minutes_range` [low, high] as well when the effort varies a lot.
  - `sizing_basis`: for each of the three values, "stated" only if the sources say it; otherwise "estimated".
  - `judgment_reason_stated`: for judgment steps and judgment decisions, true if the sources state the criteria or
    reason the person uses; false if it is left to the person's judgment with no stated rule. Leave it null for
    rule-based steps and simple checks (e.g. "Has Insightly access?").
- Pain points: set `frequency` (per_day | per_week | per_month | ad_hoc) for how often the pain is felt, with
  `frequency_basis` "stated" or "estimated"; leave `frequency` null if you cannot reasonably estimate it.
- Ideas for FUTURE automation mentioned by speakers belong in pain_points (as the problem they would
  solve) or open_questions — do not model them as current-state activities.
- If the same step appears in multiple sources under different wording, merge it into one activity.

""" + prompt_block()


def _files_hash(files: List[Tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for name, data in sorted(files, key=lambda x: x[0]):
        h.update(name.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:16]


def _demo_hash() -> Optional[str]:
    try:
        files = [(p.name, p.read_bytes()) for p in sorted(DEMO_DIR.glob("*.md"))]
        return _files_hash(files) if files else None
    except Exception:
        return None


def _load_cache(key: str) -> Optional[ProcessModel]:
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        return ProcessModel(**json.loads(p.read_text()))
    if key == _demo_hash():
        demo = DEMO_DIR / "extraction_sales_process.json"
        if demo.exists():
            return ProcessModel(**json.loads(demo.read_text()))
    return None


def _save_cache(key: str, model: ProcessModel) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(model.model_dump_json(indent=2))


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # tolerate leading prose before the first brace
    i = s.find("{")
    return s[i:] if i > 0 else s


def _call_claude(source_text: str, process_hint: str, feedback: str = "") -> str:
    import anthropic
    client = anthropic.Anthropic()
    schema_json = json.dumps(ProcessModel.model_json_schema(), indent=0)
    user = (
        f"Process being mapped: {process_hint or 'infer from materials'}\n\n"
        f"JSON schema (Pydantic):\n{schema_json}\n\n"
        f"SOURCE MATERIALS:\n{source_text}\n\n"
        "Return the ProcessModel JSON now."
    )
    if feedback:
        user += f"\n\nYour previous output failed validation:\n{feedback}\nFix these and return the full JSON again."
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def extract(
    parsed: List[ParsedFile],
    raw_files: List[Tuple[str, bytes]],
    process_hint: str = "",
    force_live: bool = False,
) -> Tuple[ProcessModel, str]:
    """
    Returns (model, mode) where mode is 'cached' or 'live'.
    Raises RuntimeError with a readable message if neither path can produce a valid model.
    """
    key = _files_hash(raw_files)

    if not force_live:
        cached = _load_cache(key)
        if cached is not None:
            return cached, "cached"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "No cached extraction for these files and ANTHROPIC_API_KEY is not set. "
            "Either set the key in .env or upload the demo transcripts from demo_data/."
        )

    source_text = to_prompt_text(parsed)
    feedback = ""
    last_err = ""
    for attempt in range(2):
        raw = _call_claude(source_text, process_hint, feedback)
        try:
            data = json.loads(_strip_fences(raw))
            model = ProcessModel(**data)
            problems = model.validate_graph()
            if problems:
                raise ValueError("; ".join(problems[:12]))
            _save_cache(key, model)
            return model, "live"
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_err = str(e)[:3000]
            feedback = last_err
    raise RuntimeError(f"Extraction failed validation after retry: {last_err}")
