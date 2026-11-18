"""
Stage 1 data contract.

Everything in the app reads from and writes to this model:
  parse  -> chunks (SourceRef)
  extract -> ProcessModel (validated here)
  review  -> edits ProcessModel
  render  -> ProcessModel -> swimlane

Stage 2 (analysis) will consume the same ProcessModel. Do not change field names
without updating the extraction prompt in ingest/extract.py.
"""
from __future__ import annotations

import json
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


StepType = Literal["deterministic", "judgment", "unclear"]
NodeKind = Literal["activity", "decision", "subprocess", "start", "end"]
Purpose = Literal["intent", "execution", "audit"]
Control = Literal["C0", "C1", "C2", "C3", "C4", "C5"]
Basis = Literal["stated", "estimated"]
Frequency = Literal["per_day", "per_week", "per_month", "ad_hoc"]


class SourceRef(BaseModel):
    """Where a fact came from. file + location (timestamp, page, or chunk id)."""
    file: str
    location: str = ""           # e.g. "00:18:00", "p.3", "chunk 4"
    quote: str = ""              # short supporting excerpt (<= 25 words)


class SizingBasis(BaseModel):
    """Whether each sizing value is stated in the sources or estimated by the agent / reviewer."""
    purpose: Basis = "estimated"
    control_now: Basis = "estimated"
    human_minutes_per_cycle: Basis = "estimated"


class StepSizing(BaseModel):
    """Documentation of the per-step sizing fields carried by Activity and Decision (CGS opportunity scoring:
    how much human effort the step takes today). Kept as a model for reference / reuse."""
    purpose: Optional[Purpose] = None                 # intent: sets goal/direction · execution: does the work · audit: checks/approves
    control_now: Optional[Control] = None             # C0 manual … C5 strategic operator
    human_minutes_per_cycle: Optional[float] = None   # typical human minutes each time the process runs
    human_minutes_range: Optional[List[float]] = None # [low, high] when a range is more honest than one number
    sizing_basis: SizingBasis = Field(default_factory=SizingBasis)
    judgment_reason_stated: Optional[bool] = None     # judgment steps/decisions: are the criteria/reason stated?


class ProcessMeta(BaseModel):
    name: str
    owner: str = ""
    scope: str = ""
    summary: str = ""            # 2-3 sentence plain-language description


class Role(BaseModel):
    id: str                      # e.g. "R1"
    name: str                    # swimlane label, e.g. "Lead Owner"
    description: str = ""
    is_external: bool = False    # customers/leads/vendors outside CGS


class Activity(BaseModel):
    id: str                      # e.g. "A1"
    name: str                    # short verb phrase, e.g. "Contact lead"
    role_id: str
    kind: NodeKind = "activity"
    order: int                   # rough left-to-right position
    description: str = ""
    notes: str = ""              # short text callout shown on the diagram (CGS standard: detail lives in comments, not labels)
    tools: List[str] = Field(default_factory=list)       # systems used, e.g. ["Insightly"]
    assumptions: List[str] = Field(default_factory=list) # unstated assumptions surfaced
    step_type: StepType = "unclear"                      # rule-based vs human judgment
    source_refs: List[SourceRef] = Field(default_factory=list)
    # sizing for CGS opportunity scoring (see StepSizing)
    purpose: Optional[Purpose] = None
    control_now: Optional[Control] = None
    human_minutes_per_cycle: Optional[float] = None
    human_minutes_range: Optional[List[float]] = None
    sizing_basis: SizingBasis = Field(default_factory=SizingBasis)
    judgment_reason_stated: Optional[bool] = None


class Branch(BaseModel):
    label: str                   # "Yes", "No", "Not yet"
    next_id: str                 # activity/decision id this branch leads to


class Decision(BaseModel):
    id: str                      # e.g. "D1"
    question: str                # "Has Insightly access?"
    role_id: str
    order: int
    branches: List[Branch] = Field(default_factory=list)
    source_refs: List[SourceRef] = Field(default_factory=list)
    # sizing for CGS opportunity scoring (see StepSizing)
    purpose: Optional[Purpose] = None
    control_now: Optional[Control] = None
    human_minutes_per_cycle: Optional[float] = None
    human_minutes_range: Optional[List[float]] = None
    sizing_basis: SizingBasis = Field(default_factory=SizingBasis)
    judgment_reason_stated: Optional[bool] = None


class Edge(BaseModel):
    """Sequence flow between two nodes (activities or decisions)."""
    from_id: str
    to_id: str
    label: str = ""              # branch label if this leaves a decision


class Handoff(BaseModel):
    from_role_id: str
    to_role_id: str
    at_node_id: str              # the node where ownership changes
    description: str = ""


class Dependency(BaseModel):
    node_id: str
    depends_on: str              # free text or node id
    type: str = "prerequisite"   # prerequisite | system | approval | information


class PainPoint(BaseModel):
    id: str
    node_id: Optional[str] = None
    description: str
    category: str = "friction"   # friction | rework | delay | manual | ownership | missing-rule
    source_refs: List[SourceRef] = Field(default_factory=list)
    frequency: Optional[Frequency] = None             # how often the pain is felt; None = not stated, no estimate
    frequency_basis: Basis = "estimated"


class ProcessModel(BaseModel):
    process: ProcessMeta
    roles: List[Role]
    activities: List[Activity]
    decisions: List[Decision] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
    handoffs: List[Handoff] = Field(default_factory=list)
    dependencies: List[Dependency] = Field(default_factory=list)
    pain_points: List[PainPoint] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)

    # ---- helpers -----------------------------------------------------------
    def node_ids(self) -> set[str]:
        return {a.id for a in self.activities} | {d.id for d in self.decisions}

    def role_name(self, role_id: str) -> str:
        for r in self.roles:
            if r.id == role_id:
                return r.name
        return role_id

    def validate_graph(self) -> List[str]:
        """Return a list of integrity problems (empty list == clean)."""
        problems: List[str] = []
        ids = self.node_ids()
        role_ids = {r.id for r in self.roles}
        for a in self.activities:
            if a.role_id not in role_ids:
                problems.append(f"Activity {a.id} references unknown role {a.role_id}")
        for d in self.decisions:
            if d.role_id not in role_ids:
                problems.append(f"Decision {d.id} references unknown role {d.role_id}")
            for b in d.branches:
                if b.next_id not in ids:
                    problems.append(f"Decision {d.id} branch '{b.label}' -> unknown node {b.next_id}")
        for e in self.edges:
            if e.from_id not in ids or e.to_id not in ids:
                problems.append(f"Edge {e.from_id}->{e.to_id} references unknown node")
        for p in self.pain_points:
            if p.node_id and p.node_id not in ids:
                problems.append(f"Pain point {p.id} references unknown node {p.node_id}")
        return problems


# ---------------------------------------------------------------------------
# Agent run artefacts (multi-step extraction)
# ---------------------------------------------------------------------------

ReconKind = Literal["merged", "conflict", "single-source", "renamed", "excluded"]
Severity = Literal["high", "medium", "low"]
IssueType = Literal["missing_step", "missing_branch", "wrong_role", "wrong_order",
                    "unsupported", "weak_evidence", "standards"]


DiscrepancyType = Literal["missing_in_source", "order", "role", "logic", "terminology", "detail"]


class Alignment(BaseModel):
    """One step lined up across the per-source models (node id per source, or None if absent)."""
    step: str
    kind: str = "activity"
    per_source: dict[str, Optional[str]] = Field(default_factory=dict)


class Discrepancy(BaseModel):
    """Where the sources disagree or one source is silent."""
    id: str                      # "X1"
    type: DiscrepancyType
    severity: Severity
    subject: str
    per_source: dict[str, str] = Field(default_factory=dict)    # file -> what that source says
    sources: List[SourceRef] = Field(default_factory=list)


class ReconItem(BaseModel):
    """One decision the reconciler made when combining per-source models."""
    kind: ReconKind
    subject: str                 # short title, e.g. "When the opportunity meeting is scheduled"
    detail: str                  # what each source said
    resolution: str              # what the merged model does and why
    discrepancy_ids: List[str] = Field(default_factory=list)
    node_ids: List[str] = Field(default_factory=list)
    sources: List[SourceRef] = Field(default_factory=list)


class ChecklistItem(BaseModel):
    """Built by the verifier from the sources ONLY, before it sees the model (avoids anchoring)."""
    id: str                      # "C1"
    kind: Literal["activity", "decision", "loop", "handoff", "subprocess", "end"]
    description: str
    role: str = ""
    evidence: List[SourceRef] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """One issue the verifier found; the reviser decides whether to apply it."""
    id: str
    round: int
    severity: Severity
    issue: IssueType
    description: str
    fix: str                     # verifier's suggested fix
    applied: bool = True         # set by the reviser
    reviser_note: str = ""       # why the reviser applied / rejected it
    checklist_ids: List[str] = Field(default_factory=list)
    node_ids: List[str] = Field(default_factory=list)
    evidence: List[SourceRef] = Field(default_factory=list)


class AgentStep(BaseModel):
    id: str
    name: str                    # "Extract — workshop transcript"
    agent: str                   # CGS agent name shown in the UI
    kind: Literal["code", "llm"]
    summary: str
    model: Optional[str] = None  # which LLM ran this step
    artifact: Optional[str] = None   # key of the JSON this step produced (see AgentRun.artifact)
    duration_s: Optional[float] = None
    status: Literal["ok", "warning", "error"] = "ok"


class AgentRun(BaseModel):
    mode: Literal["live", "replay"]
    steps: List[AgentStep]
    per_source: dict[str, ProcessModel] = Field(default_factory=dict)
    alignment: List[Alignment] = Field(default_factory=list)
    discrepancies: List[Discrepancy] = Field(default_factory=list)
    draft: Optional[ProcessModel] = None          # reconciled model before verification
    reconciliation: List[ReconItem] = Field(default_factory=list)
    checklist: List[ChecklistItem] = Field(default_factory=list)
    findings: List[ReviewFinding] = Field(default_factory=list)
    revisions: dict[int, ProcessModel] = Field(default_factory=dict)   # round -> model after reviser
    final: ProcessModel

    def artifact(self, key: str):
        """JSON-serialisable output of one step, for the 'view JSON' panels."""
        dump = lambda m: json.loads(m.model_dump_json())  # noqa: E731
        if key.startswith("per_source:"):
            return dump(self.per_source[key.split(":", 1)[1]])
        if key == "compare":
            return {"alignment": [a.model_dump() for a in self.alignment],
                    "discrepancies": [d.model_dump() for d in self.discrepancies]}
        if key == "reconcile":
            return {"model": dump(self.draft) if self.draft else None,
                    "reconciliation": [r.model_dump() for r in self.reconciliation]}
        if key == "checklist":
            return {"checklist": [c.model_dump() for c in self.checklist]}
        if key.startswith("verify:"):
            n = int(key.split(":")[1])
            return {"findings": [f.model_dump() for f in self.findings if f.round == n]}
        if key.startswith("revise:"):
            n = int(key.split(":")[1])
            return {"model": dump(self.revisions[n]) if n in self.revisions else None,
                    "decisions": [{"id": f.id, "applied": f.applied, "reviser_note": f.reviser_note}
                                  for f in self.findings if f.round == n]}
        if key == "final":
            return dump(self.final)
        return None


JSON_SCHEMA_FOR_PROMPT = ProcessModel.model_json_schema()
