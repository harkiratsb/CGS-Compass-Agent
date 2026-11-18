"""Framework data models: every framework input is an Answer; code does the scoring."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from schema import AgentStep, SourceRef

Basis = Literal["stated", "assumed", "answered"]
Conf = Literal["H", "M", "L"]

YNU = ("Y", "N", "Unknown")
YPN = ("Y", "Partly", "N")
YN = ("Y", "N")
SCORE = (1, 2, 3, 4, 5)
R_MOVE = ("Move", "Remove")

R_KEYS = [f"R{i}" for i in range(1, 8)]
D_KEYS = ["D1", "D2", "D3"]
DIAL_KEYS = ["rule_clarity", "input_structure", "variation", "judgment", "checkability", "stakes"]
A_KEYS = ["A1", "A2", "A3", "A4"]
READINESS_KEYS = ["data_systems", "process_clarity", "bus_factor", "governance", "people"]
GATE_KEYS = [str(i) for i in range(1, 9)]
CONTEXT_KEYS = ["outcome", "goal", "measures", "baselines", "volume", "context_read"]
WORKSHEET_KEYS = ["type_today", "channel", "system", "volume", "minutes", "notes"]

Call = Literal["Keep", "Remove", "Combine", "Move", "Reimagine", "Fix the map"]
StepType = Literal["Rules", "Agent", "Human approval", "Human task"]


class Answer(BaseModel):
    value: Any = None
    basis: Basis = "assumed"
    reason: str = ""
    confidence: Conf = "L"
    quotes: list[SourceRef] = Field(default_factory=list)
    question: str = ""
    derived: bool = False
    flags: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stated_needs_quote(self):
        if self.basis == "stated" and not self.quotes and not self.derived:
            self.basis = "assumed"
            self.flags.append("no quote")
        return self


def assumed(value: Any, question: str, reason: str = "No evidence in the materials") -> Answer:
    return Answer(value=value, basis="assumed", reason=reason, confidence="L", question=question)


class ChainStep(BaseModel):
    name: str
    type: StepType
    role_id: str | None = None


class TierResult(BaseModel):
    standardize_first: bool
    tier: Literal["Human", "Rules", "Agent", "Mixed"]
    approval: bool = False
    human_task: bool = False
    error_budget: bool = False
    rule_fired: int


class Recommendation(BaseModel):
    action: str
    wave: int | None
    harden_first: bool = False
    chain: list[ChainStep] = Field(default_factory=list)


class StepAssessment(BaseModel):
    step_id: str
    worksheet: dict[str, Answer]
    r_pass1: dict[str, Answer]
    r_pass2: dict[str, Answer] = Field(default_factory=dict)
    r_move: Answer | None = None          # "Move" or "Remove" (R4)
    accountability: Answer | None = None
    combine_with: Answer | None = None    # value = step id
    call: Call | None = None
    combine_target: str | None = None
    protected: bool = False
    tier1: dict[str, Answer] = Field(default_factory=dict)
    dials: dict[str, Answer] = Field(default_factory=dict)
    agent_test: dict[str, Answer] = Field(default_factory=dict)
    tier: TierResult | None = None
    readiness: dict[str, Answer] = Field(default_factory=dict)  # per-step overrides only
    readiness_min: int | None = None
    recommendation: Recommendation | None = None
    specs: list[dict] = Field(default_factory=list)
    made_unnecessary_by: str | None = None


class MapCheck(BaseModel):
    type: Literal["alternatives_as_sequence", "note_is_hidden_step", "duplicate_gate",
                  "sop_map_mismatch", "dead_end"]
    step_ids: list[str]
    question: str


class FollowUp(BaseModel):
    path: str
    question: str
    current: Any = None


class FrameworkRun(BaseModel):
    mode: Literal["live", "replay", "fixture"]
    map_reviewed: bool
    success_end: str | None = None
    doc_gate: dict[str, Answer]
    gate_score: float = 0
    gate_band: Literal["High", "Medium", "Low"] = "Low"
    context: dict[str, Answer]
    map_checks: list[MapCheck] = Field(default_factory=list)
    readiness_defaults: dict[str, Answer]
    steps: dict[str, StepAssessment]
    redesign_potential: int = 1
    posture: list[str] = Field(default_factory=list)
    waves: dict[int, list[str]] = Field(default_factory=dict)
    follow_ups: list[FollowUp] = Field(default_factory=list)
    summary_text: str = ""
    first_actions: list[str] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)


_STEP_DICTS = {"worksheet", "r_pass1", "r_pass2", "tier1", "dials", "agent_test", "readiness"}
_STEP_SINGLES = {"r_move", "accountability", "combine_with"}


def get_answer(run: FrameworkRun, path: str) -> Answer:
    """Resolve a dotted answer path, e.g. "steps.A2.dials.stakes". Raises KeyError."""
    parts = path.split(".")
    try:
        if parts[0] in ("doc_gate", "context", "readiness_defaults") and len(parts) == 2:
            return getattr(run, parts[0])[parts[1]]
        if parts[0] == "steps" and len(parts) >= 3:
            step = run.steps[parts[1]]
            if len(parts) == 4 and parts[2] in _STEP_DICTS:
                return getattr(step, parts[2])[parts[3]]
            if len(parts) == 3 and parts[2] in _STEP_SINGLES:
                ans = getattr(step, parts[2])
                if ans is not None:
                    return ans
    except KeyError:
        pass
    raise KeyError(path)


def allowed_values(path: str) -> tuple | None:
    """Allowed values for an answer path; None means free text or a number."""
    parts = path.split(".")
    if parts[0] == "doc_gate":
        return YPN
    if parts[0] == "readiness_defaults":
        return SCORE
    if parts[0] == "steps" and len(parts) >= 3:
        family = parts[2]
        if family in ("r_pass1", "r_pass2"):
            return YNU
        if family == "r_move":
            return R_MOVE
        if family == "accountability":
            return YN
        if family == "tier1":
            return YPN
        if family in ("dials", "readiness"):
            return SCORE
        if family == "agent_test":
            return YN
    return None
