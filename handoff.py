"""
Pain-point handoff: ProcessModel -> the JSON that CGS opportunity mapping consumes.

Format: cgs.compass.painpoints/v0.2 — CGS's proposed v0.1 with small, additive adjustments (see FORMAT_NOTES and the
written description shared with CGS). Every v0.1 field is present with the same name and allowed values.

How each value is produced
  process.*                 intake fields in the app (account, process id, function ref, definition status)
  activities[]              one entry per task and decision (not start/end events); sizing fields come from the
                            model (agent-extracted, reviewer-editable), each marked "stated" or "estimated"
  pain_points[].type        mapped from the agent's category (TYPE_MAP)
  pain_points[].role_ref    the lane (role) of the step the pain point is on
  data_touched              tools on that step (CGS tool list) + CGS tools named in the description
  human_judgment            present = the step is a judgment step or a decision; reason_stated from the step
  evidence[].grounded       null: no automated grounding check is run (agreed with CGS); treat as unverified
  confidence                rule-based from evidence: 0.9 two+ sources, 0.7 one source, 0.4 no quote; -0.1 if unlinked
Roles, not names: speaker names found in the source transcripts are replaced with "[team member]" in every text
field as a safety net; the extraction prompt already asks the agent to write roles instead of names.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from cgs_tools import CGS_TOOLS, ToolTag, resolve, tags_for
from schema import ProcessModel

SCHEMA = "cgs.compass.painpoints/v0.2"

TYPE_MAP = {
    "delay": "delay",
    "rework": "rework",
    "manual": "manual_data",
    "ownership": "handoff",
    "missing-rule": "unclear_decision",
    "friction": "other",
}

# CGS tools that live inside the Google Workspace tenant (an agent there can reach their data)
IN_WORKSPACE = {"TL-01", "TL-02", "TL-03", "TL-04", "TL-16", "TL-17", "TL-18", "TL-32"}

DEFINITION_STATUS = ("draft", "reviewed", "approved")


# ------------------------------------------------------------------ names -> roles
def speaker_names(files: Iterable[tuple]) -> List[str]:
    """Personal names from transcript speaker labels ("**Jane Doe:**" or "Jane Doe:"), full and first names."""
    names = set()
    for _, data in files:
        text = data.decode("utf-8", "ignore") if isinstance(data, (bytes, bytearray)) else str(data)
        for m in re.finditer(r"^\s*\**([A-Z][a-z]+(?: [A-Z][a-zA-Z'-]+){1,2})\**\s*:", text, re.M):
            full = m.group(1)
            names.add(full)
            names.add(full.split()[0])
    return with_nicknames(sorted(names))


NICKNAMES = {"Elizabeth": ["Liz", "Beth", "Eliza"], "Stephan": ["Steph"], "Stephen": ["Steve"], "Gregory": ["Greg", "Gregg"],
             "Jordyn": ["Jordan"], "Katherine": ["Kate", "Kathy"], "Michael": ["Mike"], "Robert": ["Rob", "Bob"],
             "William": ["Will", "Bill"], "Jennifer": ["Jen"], "Christopher": ["Chris"], "Alexander": ["Alex"]}


def with_nicknames(names: List[str]) -> List[str]:
    out = set(names)
    for n in names:
        out.update(NICKNAMES.get(n, []))
    return sorted(out, key=len, reverse=True)


def _scrub(text: str, names: List[str]) -> str:
    for n in names:
        text = re.sub(rf"\b{re.escape(n)}\b('s)?", lambda m: "[team member]" + (m.group(1) or ""), text)
    return text


# ------------------------------------------------------------------ helpers
def _confidence(refs, linked: bool) -> float:
    files = {r.file for r in refs if r.quote}
    c = 0.9 if len(files) >= 2 else 0.7 if files else 0.4
    return round(max(0.1, c - (0 if linked else 0.1)), 2)


def _data_touched(tags: List[ToolTag]) -> List[dict]:
    out = []
    for t in tags:
        out.append({
            "system": t.label if t.on_list else t.raw,
            "tool_id": t.tool_id,
            "on_cgs_list": t.on_list,
            "in_workspace": (t.tool_id in IN_WORKSPACE) if t.on_list else None,
            "drive_ref": None,
        })
    return out


def _tools_named_in(text: str) -> List[str]:
    """CGS tool names mentioned in free text (e.g. 'Insightly' in a pain point description)."""
    found = []
    low = text.lower()
    # distinctive names only: generic words (calendar, drive, chat, tasks) give false hits in free text
    names = {"Insightly", "Gmail", "Gemini", "NotebookLM", "Claude", "Claude Code", "LinkedIn", "GitHub", "Obsidian",
             "QuickBooks", "QuickBooks Online", "QuickBooks Payroll", "Lucidchart", "Mural", "Google Docs",
             "Google Drive", "Google Sheets", "Google Slides", "Google Calendar", "Google Meet", "Google Chat",
             "Google Tasks", "Google Sites", "Google Vids"}
    for n in sorted(names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(n.lower())}\b", low) and resolve(n).on_list:
            found.append(n)
    return found


# ------------------------------------------------------------------ main
def build_handoff(model: ProcessModel, *, account_id: str = "", process_id: str = "", function_ref: str = "",
                  definition_status: str = "draft", swimlane_ref: Optional[str] = None,
                  names: Optional[List[str]] = None, mode: Optional[str] = None,
                  models_used: Optional[List[str]] = None) -> dict:
    names = names or []
    S = lambda t: _scrub(t or "", names)  # noqa: E731
    roles = {r.id: r.name for r in model.roles}
    acts = {a.id: a for a in model.activities}
    decs = {d.id: d for d in model.decisions}

    def node(nid):
        return acts.get(nid) or decs.get(nid)

    def judgment(nid) -> dict:
        n = node(nid)
        if n is None:
            return {"present": False, "reason_stated": None}
        # judgment steps; decisions count when the agent judged them (a reason flag), not simple checks like "Has access?"
        present = getattr(n, "step_type", "") == "judgment" or (nid in decs and n.judgment_reason_stated is not None)
        return {"present": present, "reason_stated": n.judgment_reason_stated if present else None}

    activities = []
    for n in sorted(list(model.activities) + list(model.decisions), key=lambda x: (x.order, x.id)):
        if getattr(n, "kind", "decision") in ("start", "end"):
            continue
        entry = {
            "activity_ref": n.id,
            "name": S(getattr(n, "name", None) or getattr(n, "question", "")),
            "role_ref": roles.get(n.role_id, n.role_id),
            "purpose": n.purpose,
            "control_now": n.control_now,
            "human_minutes_per_cycle": n.human_minutes_per_cycle,
        }
        if n.human_minutes_range:
            entry["human_minutes_range"] = [float(v) for v in n.human_minutes_range[:2]]
        entry["basis"] = n.sizing_basis.model_dump()
        hj = judgment(n.id)
        if hj["present"]:
            entry["human_judgment"] = hj
        tags = tags_for(getattr(n, "tools", []) or [])
        if tags:
            entry["tools"] = [t.text for t in tags]
        activities.append(entry)

    pain_points = []
    for p in model.pain_points:
        n = node(p.node_id) if p.node_id else None
        tags = tags_for((getattr(n, "tools", []) or []) + _tools_named_in(p.description))
        pain_points.append({
            "id": p.id if p.id.startswith("PP-") else f"PP-{int(re.sub(r'[^0-9]', '', p.id) or 0):02d}",
            "activity_ref": p.node_id,
            "role_ref": roles.get(n.role_id) if n else None,
            "description": S(p.description),
            "type": TYPE_MAP.get(p.category, "other"),
            "source_category": p.category,
            "frequency": p.frequency,
            "frequency_basis": p.frequency_basis if p.frequency else None,
            "data_touched": _data_touched(tags),
            "human_judgment": judgment(p.node_id) if p.node_id else {"present": False, "reason_stated": None},
            "evidence": [{"transcript_ref": f"{r.file} @ {r.location}" if r.location else r.file,
                          "quote": S(r.quote)[:200],
                          "grounded": None}
                         for r in p.source_refs],
            "confidence": _confidence(p.source_refs, bool(n)),
        })

    if definition_status not in DEFINITION_STATUS:
        definition_status = "draft"
    return {
        "schema": SCHEMA,
        "account_id": account_id or None,
        "generated": {
            "by": "CGS Stage 1 process discovery",
            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "mode": mode,
            "models": models_used or [],
        },
        "process": {
            "id": process_id or None,
            "name": S(model.process.name),
            "function_ref": function_ref or None,
            "owner_role": S(model.process.owner) or None,
            "definition_status": definition_status,
            "swimlane_ref": swimlane_ref,
        },
        "activities": activities,
        "pain_points": pain_points,
        "open_questions": [S(q) for q in model.open_questions],
    }


def check_handoff(h: dict, names: Optional[List[str]] = None) -> List[str]:
    """Light validation against the format's allowed values; returns a list of problems (empty = valid)."""
    probs = []
    if h.get("schema") != SCHEMA:
        probs.append("schema id")
    if h["process"]["definition_status"] not in DEFINITION_STATUS:
        probs.append("process.definition_status")
    refs = {a["activity_ref"] for a in h["activities"]}
    for a in h["activities"]:
        if a["purpose"] not in (None, "intent", "execution", "audit"):
            probs.append(f"{a['activity_ref']}: purpose")
        if a["control_now"] not in (None, "C0", "C1", "C2", "C3", "C4", "C5"):
            probs.append(f"{a['activity_ref']}: control_now")
        if a["human_minutes_per_cycle"] is not None and not isinstance(a["human_minutes_per_cycle"], (int, float)):
            probs.append(f"{a['activity_ref']}: minutes")
    for p in h["pain_points"]:
        if p["type"] not in ("delay", "rework", "handoff", "manual_data", "unclear_decision", "other"):
            probs.append(f"{p['id']}: type")
        if p["frequency"] not in (None, "per_day", "per_week", "per_month", "ad_hoc"):
            probs.append(f"{p['id']}: frequency")
        if p["activity_ref"] and p["activity_ref"] not in refs:
            probs.append(f"{p['id']}: activity_ref {p['activity_ref']} not in activities")
        for e in p["evidence"]:
            if len(e["quote"]) > 200:
                probs.append(f"{p['id']}: quote over 200 characters")
    if names:
        blob = str(h)
        leaked = [n for n in names if re.search(rf"\b{re.escape(n)}\b", blob)]
        if leaked:
            probs.append("names found: " + ", ".join(leaked))
    return probs
