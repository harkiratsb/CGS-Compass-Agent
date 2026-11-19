"""Framework data models: every framework input is an Answer; code does the scoring."""
from __future__ import annotations

import json
from pathlib import Path
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
    call_rule: str | None = None          # R1..R7 that fired in part2_call; None for Keep
    tier1: dict[str, Answer] = Field(default_factory=dict)
    dials: dict[str, Answer] = Field(default_factory=dict)
    agent_test: dict[str, Answer] = Field(default_factory=dict)
    tier: TierResult | None = None
    readiness: dict[str, Answer] = Field(default_factory=dict)  # per-step overrides only
    readiness_min: int | None = None
    recommendation: Recommendation | None = None
    specs: list[dict] = Field(default_factory=list)
    ai_chain: list[ChainStep] = Field(default_factory=list)   # set by the specs step; used while compatible
    ai_made_unnecessary_by: str | None = None   # the loop pass's AI value; never changed by code
    made_unnecessary_by: str | None = None      # effective mark, derived from the AI value every code pass


class MapCheck(BaseModel):
    type: Literal["alternatives_as_sequence", "note_is_hidden_step", "duplicate_gate",
                  "sop_map_mismatch", "dead_end"]
    step_ids: list[str]
    question: str


class FollowUp(BaseModel):
    path: str
    question: str
    current: Any = None


class TopQuestion(BaseModel):
    """One of the top follow-up questions: one or more answer paths asked together."""
    question: str
    paths: list[str]
    labels: dict[str, str] = Field(default_factory=dict)    # path -> the member's own question
    current: dict[str, Any] = Field(default_factory=dict)   # path -> value now
    effect: str = ""                                        # what answering it could change
    step_id: str | None = None


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
    top_questions: list[TopQuestion] = Field(default_factory=list)   # cache, see framework/followups.py
    top_questions_key: str = ""                                      # the run state it was computed for
    summary_text: str = ""
    summary_answers_key: str = ""     # the app answers the AI summary was written with (see rules.answers_key)
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


def allowed_values(path: str, step_ids: list[str] | None = None) -> tuple | None:
    """Allowed values for an answer path; None means free text or a number.

    combine_with takes another step's id: pass the run's step ids to get them."""
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
        if family == "combine_with" and step_ids is not None:
            return tuple(i for i in step_ids if i != parts[1])
        if family == "tier1":
            return YPN
        if family in ("dials", "readiness"):
            return SCORE
        if family == "agent_test":
            return YN
    return None


# ------------------------------------------------------- blank run (all assumed)

_GATE_Q = {
    "1": "Are the start and every end of the process marked on the map?",
    "2": "Are the swimlanes roles rather than people?",
    "3": "Is every task named as a verb plus a noun?",
    "4": "Is every decision a question with labelled outcomes?",
    "5": "Does the map show the main path and the loops?",
    "6": "Does every step name the system it uses?",
    "7": "Is every subprocess linked and owned?",
    "8": "Is there an SOP (how-to detail) for the human steps?",
}
_CONTEXT_Q = {
    "outcome": "What outcome does this process deliver?",
    "goal": "What is the goal for improving this process?",
    "measures": "How is success measured?",
    "baselines": "What are today's values for those measures (baselines)?",
    "volume": "How many times does the process run, and per what period?",
    "context_read": "What context should a reader know before scoring this process?",
}
_READINESS_Q = {
    "data_systems": "Data and systems (1-5): is there an owned system of record the work can reach?",
    "process_clarity": "Process clarity (1-5): is the process mapped with owners and a complete SOP?",
    "bus_factor": "Bus factor (1-5): how many people can do this work (1, 2, or 3+)?",
    "governance": "Governance (1-5): is there a named owner, approvals, limits, escalation and a record?",
    "people": "People (1-5): resistance, champions, or broad literacy with roles redefined?",
}
_WORKSHEET_Q = {
    "type_today": "Is {s} today Rules, Agent, Human approval or a Human task?",
    "channel": "Which channel or variant does {s} use?",
    "system": "Which system is used for {s}?",
    "volume": "How often does {s} happen?",
    "minutes": "How many minutes does {s} take each time?",
    "notes": "Anything else to note about {s}?",
}
_R_Q = {
    "R1": "Is the outcome reached without {s}?",
    "R2": "Does {s} exist only for an access limit, handoff, info passing, habit or untrusted systems?",
    "R3": "Does {s} re-key information that already exists?",
    "R4": "Is {s} mostly waiting?",
    "R5": "Does {s} fix a problem caused earlier in the process?",
    "R6": "Could {s} merge with an adjacent step done by the same role?",
    "R7": "Does the map draw two things as one, or alternatives as a sequence, at {s}?",
}
_D_Q = {
    "D1": "Is {s} digital?",
    "D2": "Is there a standard, written way to do {s}?",
    "D3": "Are the inputs to {s} findable (named fields or files)?",
}
_DIAL_Q = {
    "rule_clarity": "Rule clarity for {s} (1 nothing written, 3 written with judgment, 5 fully written)?",
    "input_structure": "Input structure for {s} (1 free text, 3 mix, 5 fields)?",
    "variation": "Variation for {s} (1 alike, 3 known variants, 5 each differs)?",
    "judgment": "Judgment for {s} (1 none, 3 a few factors, 5 context and trade-offs)?",
    "checkability": "Checkability for {s} (1 must redo, 3 effort, 5 a glance)?",
    "stakes": "Stakes for {s} (1 undoable and internal, 3 client or money, 5 irreversible or the relationship is the point)?",
}
_A_Q = {
    "A1": "Does {s} need free text or context?",
    "A2": "Does {s} have high variation?",
    "A3": "Would rules give about 80% of the gain for {s}?",
    "A4": "Is the extra gain from an agent worth it for {s}?",
}
_TYPE_HINT = {"deterministic": "Rules", "judgment": "Human task"}


def _derived(value: Any) -> Answer:
    return Answer(value=value, basis="stated", derived=True, confidence="M",
                  reason="Prefilled from the Stage 1 map")


def blank_run(model, mode: str, s=None) -> FrameworkRun:
    """A run for `model` where every input is `assumed(default, question)`.

    Worksheet system / minutes / type today are prefilled from Stage 1 where present.
    """
    from framework.graph import graph_facts
    from framework.settings import load_settings

    s = s or load_settings()
    d = s.defaults
    facts = graph_facts(model)
    acts = {a.id: a for a in model.activities}
    decs = {x.id: x for x in model.decisions}

    def fam(questions: dict[str, str], default, label: str = "") -> dict[str, Answer]:
        return {k: assumed(default(k) if callable(default) else default, q.format(s=label))
                for k, q in questions.items()}

    steps: dict[str, StepAssessment] = {}
    for sid in facts.steps:
        act, dec = acts.get(sid), decs.get(sid)
        label = f'"{act.name}"' if act else f'the decision "{dec.question}"' if dec else sid
        worksheet = fam(_WORKSHEET_Q, None, label)
        minutes = act.human_minutes_per_cycle if act else dec.human_minutes_per_cycle if dec else None
        if act and act.tools:
            worksheet["system"] = _derived(", ".join(act.tools))
        if minutes is not None:
            worksheet["minutes"] = _derived(minutes)
        hint = "Human task" if dec else _TYPE_HINT.get(act.step_type) if act else None
        if hint:
            worksheet["type_today"] = _derived(hint)
        steps[sid] = StepAssessment(
            step_id=sid,
            worksheet=worksheet,
            r_pass1=fam(_R_Q, "Unknown", label),
            tier1=fam(_D_Q, "N", label),
            dials=fam(_DIAL_Q, lambda k: d.rule_clarity if k == "rule_clarity" else d.dial, label),
            agent_test=fam(_A_Q, "N", label),
        )
    return FrameworkRun(
        mode=mode, map_reviewed=False, success_end=facts.success_end,
        doc_gate=fam(_GATE_Q, "N"), context=fam(_CONTEXT_Q, None),
        readiness_defaults=fam(_READINESS_Q, d.readiness), steps=steps,
    )


def load_answers(run: FrameworkRun, path) -> FrameworkRun:
    """Apply a JSON file of {answer path: Answer dict} to a copy of `run`.

    Dict families get the key created or replaced; r_move / accountability /
    combine_with are set. Unknown step or family raises KeyError.
    """
    run = run.model_copy(deep=True)
    for apath, raw in json.loads(Path(path).read_text()).items():
        answer = Answer.model_validate(raw)
        parts = apath.split(".")
        if parts[0] in ("doc_gate", "context", "readiness_defaults") and len(parts) == 2:
            getattr(run, parts[0])[parts[1]] = answer
        elif parts[0] == "steps" and len(parts) >= 3:
            step = run.steps[parts[1]]
            if len(parts) == 4 and parts[2] in _STEP_DICTS:
                getattr(step, parts[2])[parts[3]] = answer
            elif len(parts) == 3 and parts[2] in _STEP_SINGLES:
                setattr(step, parts[2], answer)
            else:
                raise KeyError(apath)
        else:
            raise KeyError(apath)
    return run
