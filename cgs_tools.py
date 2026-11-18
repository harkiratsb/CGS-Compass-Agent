"""
CGS tool list and tool-tag resolution.

Steps keep the tool names the agent extracted (Activity.tools, free text). For display and handoff, each name is
resolved to a CGS tool ID from the list below, so diagrams match CGS's other process maps.

Rules (agreed with CGS):
  - Bundled entries (e.g. TL-01 = Gmail, Calendar, Meet) are tagged with the ID and keep the specific app as the
    label: "TL-01 · Calendar".
  - A tool that is not on the list keeps its plain name and is marked as not on the CGS list.
  - Documents and templates (sales plays, intro decks) are not tools; they belong in the step note.
  - Only tools the sources actually name are tagged (enforced in the extraction prompt).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# id, name, category, apps covered (label used when the bundle is matched by one of its apps)
CGS_TOOLS: List[Tuple[str, str, str, List[str]]] = [
    ("TL-01", "Google Workspace (Gmail, Calendar, Meet)", "Productivity Suite", ["Gmail", "Calendar", "Meet"]),
    ("TL-02", "Google Drive, Docs, Sheets, Slides", "Productivity Suite", ["Drive", "Docs", "Sheets", "Slides"]),
    ("TL-03", "Gemini & Gems", "AI Assistant", ["Gemini", "Gems"]),
    ("TL-04", "NotebookLM", "AI Assistant", []),
    ("TL-05", "Claude", "AI Assistant", []),
    ("TL-06", "Claude Code", "AI Development", []),
    ("TL-07", "Google Cloud (ADK, Cloud Run)", "AI Development", ["ADK", "Cloud Run"]),
    ("TL-08", "Insightly", "CRM", []),
    ("TL-09", "LinkedIn", "Social / Marketing", []),
    ("TL-10", "GitHub", "Source Control", []),
    ("TL-11", "Obsidian", "Knowledge Management", []),
    ("TL-12", "QuickBooks Online", "Finance & Accounting", []),
    ("TL-13", "QuickBooks Payroll", "Finance & Accounting", []),
    ("TL-14", "Lucidchart", "Productivity Suite", []),
    ("TL-15", "Mural", "Productivity Suite", []),
    ("TL-16", "Google Vids", "Productivity Suite", []),
    ("TL-17", "Google Chat & Spaces", "Productivity Suite", ["Chat", "Spaces"]),
    ("TL-18", "Google Tasks", "Productivity Suite", []),
    ("TL-32", "Google Sites", "Productivity Suite", []),
]

# normalised alias -> (tool id, label shown on the tag)
_ALIASES: Dict[str, Tuple[str, str]] = {
    "gmail": ("TL-01", "Gmail"), "email": ("TL-01", "Gmail"), "google mail": ("TL-01", "Gmail"),
    "calendar": ("TL-01", "Calendar"), "google calendar": ("TL-01", "Calendar"),
    "meet": ("TL-01", "Meet"), "google meet": ("TL-01", "Meet"),
    "google workspace": ("TL-01", "Google Workspace"),
    "drive": ("TL-02", "Drive"), "google drive": ("TL-02", "Drive"),
    "docs": ("TL-02", "Docs"), "google docs": ("TL-02", "Docs"), "google doc": ("TL-02", "Docs"),
    "sheets": ("TL-02", "Sheets"), "google sheets": ("TL-02", "Sheets"),
    "slides": ("TL-02", "Slides"), "google slides": ("TL-02", "Slides"),
    "gemini": ("TL-03", "Gemini"), "gems": ("TL-03", "Gems"), "gemini gems": ("TL-03", "Gems"),
    "notebooklm": ("TL-04", "NotebookLM"),
    "claude": ("TL-05", "Claude"),
    "claude code": ("TL-06", "Claude Code"),
    "google cloud": ("TL-07", "Google Cloud"), "adk": ("TL-07", "ADK"), "cloud run": ("TL-07", "Cloud Run"),
    "insightly": ("TL-08", "Insightly"), "crm": ("TL-08", "Insightly"), "insightly crm": ("TL-08", "Insightly"),
    "linkedin": ("TL-09", "LinkedIn"),
    "github": ("TL-10", "GitHub"),
    "obsidian": ("TL-11", "Obsidian"),
    "quickbooks": ("TL-12", "QuickBooks Online"), "quickbooks online": ("TL-12", "QuickBooks Online"), "qbo": ("TL-12", "QuickBooks Online"),
    "quickbooks payroll": ("TL-13", "QuickBooks Payroll"),
    "lucidchart": ("TL-14", "Lucidchart"), "lucid": ("TL-14", "Lucidchart"),
    "mural": ("TL-15", "Mural"),
    "google vids": ("TL-16", "Vids"), "vids": ("TL-16", "Vids"),
    "google chat": ("TL-17", "Chat"), "chat": ("TL-17", "Chat"), "spaces": ("TL-17", "Spaces"), "google spaces": ("TL-17", "Spaces"),
    "google tasks": ("TL-18", "Tasks"),
    "google sites": ("TL-32", "Sites"),
}
TOOL_NAME = {t[0]: t[1] for t in CGS_TOOLS}


@dataclass(frozen=True)
class ToolTag:
    tool_id: Optional[str]     # None when not on the CGS list
    label: str                 # specific app, e.g. "Calendar"
    raw: str                   # name as extracted

    @property
    def on_list(self) -> bool:
        return self.tool_id is not None

    @property
    def text(self) -> str:
        return f"{self.tool_id} · {self.label}" if self.tool_id else self.label


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s.lower())          # "Gemini (notes)" -> "gemini"
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve(name: str) -> ToolTag:
    n = _norm(name)
    if n in _ALIASES:
        tid, label = _ALIASES[n]
        return ToolTag(tid, label, name)
    for tid, full, _, _ in CGS_TOOLS:              # exact full-name match, e.g. "Google Drive, Docs, Sheets, Slides"
        if n == _norm(full):
            return ToolTag(tid, full, name)
    return ToolTag(None, name.strip(), name)


def tags_for(tools: List[str]) -> List[ToolTag]:
    """Resolve and de-duplicate (same ID + label once), keeping extraction order."""
    out, seen = [], set()
    for t in tools:
        if not t or not t.strip():
            continue
        tag = resolve(t)
        key = (tag.tool_id, tag.label.lower())
        if key not in seen:
            seen.add(key)
            out.append(tag)
    return out


def prompt_block() -> str:
    """Tool list + rules for the extraction prompt."""
    rows = "\n".join(f"  {tid} | {name}" for tid, name, _, _ in CGS_TOOLS)
    return (
        "CGS tool list (use these names in `tools`; for bundled entries name the specific app, e.g. \"Calendar\"):\n"
        f"{rows}\n"
        "Tool rules:\n"
        "- Only list a tool if the sources name it (or clearly name what it does, e.g. 'have Gemini take notes').\n"
        "  Never infer or invent tools.\n"
        "- Documents, templates and playbooks (e.g. sales plays, intro decks) are not tools: mention them in `notes`.\n"
        "- A named tool that is not on the CGS list keeps its plain name.\n"
        "- Do not list the same tool twice under different names."
    )
