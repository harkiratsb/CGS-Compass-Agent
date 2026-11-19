# Phase 3 CGS Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run CGS's Process Improvement Framework (Parts 0–5) end to end on a reviewed Stage 1 process model and produce the future-state diagram, step table, summary and follow-up questions.

**Architecture:** A new `framework/` package. AI steps answer only the framework's judgment questions (each answer carries basis, quote, reason, confidence). Deterministic code in `framework/rules.py`, driven by `framework/settings.toml`, makes every call, tier, score, posture and wave. A future-state `ProcessModel` is built from the step table and drawn with the existing renderers, extended with step-type markers. Prerequisite work adds a project file (save/open) and a pytest suite.

**Tech Stack:** Python 3.14, Streamlit, pydantic v2, Anthropic SDK, pytest, `tomllib` (stdlib).

**Spec:** `docs/superpowers/specs/2026-11-18-phase3-framework-design.md`. The method it implements, with every check, rule and anchor, is `docs/cgs-framework-starter.md`. Read both before starting any task.

## Global Constraints

- `schema.py` `ProcessModel` fields are not changed. Phase 3 only reads it (and builds new instances for the future state).
- No new runtime dependencies. `pytest` goes in a new `requirements-dev.txt`.
- The AI never makes a call, applies a threshold or computes a score. Code does, from `framework/settings.toml`.
- Every framework input is an `Answer` with `basis` ∈ {`stated`, `assumed`, `answered`}. `stated` needs a quote unless `derived=True` (computed by code from the map). Missing or invalid evidence becomes `assumed` with the framework default and a follow-up question.
- Defaults: dials 3 "assumed", Rule clarity 1; readiness 1 "unverified"; R answers "Unknown" (counts as No).
- Missing evidence never raises a score. The verifier can only downgrade (to `assumed` or lower confidence).
- A run never stops for missing inputs.
- Roles, never personal names, in every output (reuse `handoff.speaker_names` / `with_nicknames` as the safety net on exported text).
- Models: `CGS_MODEL` for AI steps, `CGS_REVIEW_MODEL` for the verifier (reuse `ingest.extract.MODEL`, `ingest.agent.REVIEW_MODEL`).
- Restart Streamlit after changing any module other than `app.py` (it doesn't reload imports).
- Commit after every task on branch `phase3-framework`. End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The AI returns unknown step ids, omits steps, or gives out-of-range values** (dial 7, "maybe", R answer "Probably"). Expected: unknown ids ignored; missing steps get all-assumed answers; invalid values become `assumed` defaults with a flag. No crash. → Task 9 test `test_parse_answers_tolerates_bad_ai_output`.
2. **A map with several end events, no path to an end, a single lane, or no decisions.** Expected: success end proposed or `None` without error; `main_path` empty when unreachable; rules still run. → Task 4 tests `test_graph_facts_edge_cases`.
3. **Removing a decision or a step whose neighbours are also removed.** Expected: the future graph stays valid (`validate_graph() == []`), with edges re-pointed to the next kept node. → Task 11 test `test_remove_chain_of_steps_keeps_graph_valid`.
4. **Opening a file that isn't a project, or a project from a different version.** Expected: a clear error message in the app and nothing in the session changed. → Task 2 test `test_load_project_rejects_bad_files`.
5. **Answering a follow-up with a value outside the allowed set** (dial "10", R "yes please"). Expected: refused with a message; the run is unchanged. → Task 10 test `test_answer_follow_up_rejects_invalid_value`.

---

## File Structure

| File | Responsibility |
|---|---|
| `requirements-dev.txt` | `pytest>=8` |
| `tests/conftest.py` | `demo_model()` fixture: the final `ProcessModel` from `demo_data/agent_run_sales_process.json` |
| `project.py` | Project file: save/open the whole working state |
| `framework/__init__.py` | empty |
| `framework/settings.toml` | every CGS number and band |
| `framework/settings.py` | load and validate settings |
| `framework/models.py` | `Answer`, `StepAssessment`, `FrameworkRun`, answer paths |
| `framework/graph.py` | graph facts: neighbours, actors, ends, main path |
| `framework/rules.py` | all CGS rules; `apply_rules`, `answer_and_rescore` |
| `framework/agent.py` | AI steps, prompts, parsing, cache/replay, `run_framework` |
| `framework/future.py` | step table → future-state `ProcessModel` + markers |
| `framework/outputs.py` | step table CSV/JSON, summary and follow-ups Markdown |
| `render/swimlane.py`, `render/drawio.py` | add `markers` parameter + legend |
| `app.py` | Stage 1 reorder, save/open/mark reviewed, Phase 3 screens |
| `scripts/phase3_live_check.py` | live AI run on the demo, agreement report, `--record` replay |
| `tests/fixtures/demo_answers.json` | curated AI answers for the acceptance test |

---

### Task 1: Test harness and settings

**Files:**
- Create: `requirements-dev.txt`, `tests/__init__.py`, `tests/conftest.py`, `framework/__init__.py`, `framework/settings.toml`, `framework/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `class Settings(BaseModel)` with the fields below; `load_settings(path: Path | None = None) -> Settings` (default path `framework/settings.toml`). Fixture `demo_model` in `tests/conftest.py`.

`settings.toml` (values from the starter):

```toml
[gate]
high = 7.0
medium = 4.0

[redesign]
# lower bounds (percent of non-Keep steps) for scores 2, 3, 4, 5
bands = [10, 25, 40, 60]

[defaults]
dial = 3
rule_clarity = 1
readiness = 1

[tier.rules]          # rule 2
clarity_min = 4
input_min = 4
variation_max = 2
judgment_max = 2

[tier.agent]          # rule 3
checkability_min = 3
judgment_min = 3
input_max = 2
variation_min = 3
approval_stakes_min = 3

[tier.human]          # rules 1 and 4
stakes = 5
judgment_min = 3
checkability_max = 2

[readiness]
ready_min = 4

[loop]
extra_passes = 1
```

`Settings` mirrors this as nested pydantic models (`gate.high`, `tier.rules.clarity_min`, …).

- [ ] **Step 1: Write the failing tests**

```python
def test_load_settings_defaults():
    s = load_settings()
    assert s.gate.high == 7.0 and s.gate.medium == 4.0
    assert s.redesign.bands == [10, 25, 40, 60]
    assert s.defaults.dial == 3 and s.defaults.rule_clarity == 1 and s.defaults.readiness == 1
    assert s.tier.agent.approval_stakes_min == 3 and s.tier.human.stakes == 5
    assert s.readiness.ready_min == 4 and s.loop.extra_passes == 1

def test_load_settings_custom_file(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text((ROOT / "framework/settings.toml").read_text().replace("high = 7.0", "high = 6.5"))
    assert load_settings(p).gate.high == 6.5

def test_load_settings_rejects_missing_key(tmp_path):
    p = tmp_path / "s.toml"; p.write_text("[gate]\nhigh = 7.0\n")
    with pytest.raises(ValueError):
        load_settings(p)

def test_demo_model_fixture(demo_model):
    assert len(demo_model.decisions) == 8 and demo_model.validate_graph() == []
```

- [ ] **Step 2: Run** `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest tests/test_settings.py -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `framework/settings.py` with `tomllib`; wrap pydantic `ValidationError` as `ValueError`. `conftest.py` loads `AgentRun` from the demo run and returns `.final`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add pytest harness and framework settings`.

---

### Task 2: Project file (save, open, mark reviewed)

**Files:**
- Create: `project.py`
- Modify: `app.py` (sidebar: "Open project" uploader; Stage 1: "Save project" download and "Mark map as reviewed" toggle)
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `schema.ProcessModel`, `schema.AgentRun`, `ingest.parse.parse_file`.
- Produces:
  - `PROJECT_VERSION = 1`
  - `class Project(BaseModel)`: `version: int`, `files: list[StoredFile]` (`name: str`, `data_b64: str`), `intake: dict[str, str]`, `model: ProcessModel`, `run: AgentRun | None`, `mode: str | None`, `map_reviewed: bool = False`, `framework: dict | None = None` (a dumped `FrameworkRun`, typed loosely here so Task 2 doesn't depend on Task 3).
  - `save_project(files: list[tuple[str, bytes]], intake: dict, model, run, mode, map_reviewed: bool, framework: dict | None) -> str` (JSON text)
  - `load_project(text: str) -> tuple[Project, list[tuple[str, bytes]]]` (raises `ProjectError(str)` with a plain message)
  - `parsed_files(files) -> list[ParsedFile]` (re-parses on load; parsing is deterministic)
- App session keys added: `ss.map_reviewed: bool`, `ss.framework: FrameworkRun | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_project_round_trip(demo_model):
    files = [("a.md", b"## 00:00:01\nhello")]
    text = save_project(files, {"process_id": "P-01"}, demo_model, None, "replay", True, None)
    proj, back = load_project(text)
    assert back == files and proj.map_reviewed is True
    assert proj.model.model_dump() == demo_model.model_dump()
    assert parsed_files(back)[0].chunks[0].location == "00:00:01"

@pytest.mark.parametrize("bad", ["not json", "{}", '{"version": 99, "files": [], "intake": {}, "model": null}'])
def test_load_project_rejects_bad_files(bad):
    with pytest.raises(ProjectError):
        load_project(bad)
```

- [ ] **Step 2: Run** `pytest tests/test_project.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `project.py`. Version mismatch message: "This project was saved by a different version of the app (version N)."
- [ ] **Step 4: Wire the app.** Open project restores `ss.files`, `ss.parsed`, `ss.model`, `ss.run`, `ss.mode`, `ss.map_reviewed`, `ss.framework` and the intake fields; on `ProjectError` show `st.error` and change nothing. Save project lives on the Stage 1 Swimlane tab next to the other downloads.
- [ ] **Step 5: Run** the tests (PASS). Then start the app (`streamlit run app.py --server.port 8502`), load the demo, Analyze, save, refresh the page, open the saved file, and confirm the swimlane comes back.
- [ ] **Step 6: Commit** `feat: save and open projects, mark map reviewed`.

---

### Task 3: Framework data models

**Files:**
- Create: `framework/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `schema.SourceRef`, `schema.AgentStep`.
- Produces:
  - `Basis = Literal["stated", "assumed", "answered"]`, `Conf = Literal["H", "M", "L"]`
  - `class Answer(BaseModel)`: `value: Any = None`, `basis: Basis = "assumed"`, `reason: str = ""`, `confidence: Conf = "L"`, `quotes: list[SourceRef] = []`, `question: str = ""`, `derived: bool = False`, `flags: list[str] = []`, `history: list[dict] = []`. Validator: `basis == "stated"` with no quotes and not `derived` → `basis = "assumed"`, append flag `"no quote"`.
  - `assumed(value, question: str, reason: str = "No evidence in the materials") -> Answer`
  - Allowed-value sets: `YNU = ("Y", "N", "Unknown")`, `YPN = ("Y", "Partly", "N")`, `YN = ("Y", "N")`, `SCORE = (1, 2, 3, 4, 5)`
  - Key lists: `R_KEYS = ["R1".."R7"]`, `D_KEYS = ["D1", "D2", "D3"]`, `DIAL_KEYS = ["rule_clarity", "input_structure", "variation", "judgment", "checkability", "stakes"]`, `A_KEYS = ["A1".."A4"]`, `READINESS_KEYS = ["data_systems", "process_clarity", "bus_factor", "governance", "people"]`, `GATE_KEYS = ["1".."8"]`, `CONTEXT_KEYS = ["outcome", "goal", "measures", "baselines", "volume", "context_read"]`, `WORKSHEET_KEYS = ["type_today", "channel", "system", "volume", "minutes", "notes"]`
  - `Call = Literal["Keep", "Remove", "Combine", "Move", "Reimagine", "Fix the map"]`
  - `StepType = Literal["Rules", "Agent", "Human approval", "Human task"]`
  - `class ChainStep(BaseModel)`: `name: str`, `type: StepType`, `role_id: str | None = None`
  - `class TierResult(BaseModel)`: `standardize_first: bool`, `tier: Literal["Human", "Rules", "Agent", "Mixed"]`, `approval: bool = False`, `human_task: bool = False`, `error_budget: bool = False`, `rule_fired: int`
  - `class Recommendation(BaseModel)`: `action: str`, `wave: int | None`, `harden_first: bool = False`, `chain: list[ChainStep] = []`
  - `class StepAssessment(BaseModel)`: `step_id: str`, `worksheet: dict[str, Answer]`, `r_pass1: dict[str, Answer]`, `r_pass2: dict[str, Answer] = {}`, `r_move: Answer | None` (value "Move" or "Remove" for R4), `accountability: Answer | None`, `combine_with: Answer | None` (value = step id), `call: Call | None = None`, `combine_target: str | None = None`, `protected: bool = False`, `tier1: dict[str, Answer] = {}`, `dials: dict[str, Answer] = {}`, `agent_test: dict[str, Answer] = {}`, `tier: TierResult | None = None`, `readiness: dict[str, Answer] = {}` (per-step overrides only), `readiness_min: int | None = None`, `recommendation: Recommendation | None = None`, `specs: list[dict] = []`, `made_unnecessary_by: str | None = None`
  - `class MapCheck(BaseModel)`: `type: Literal["alternatives_as_sequence", "note_is_hidden_step", "duplicate_gate", "sop_map_mismatch", "dead_end"]`, `step_ids: list[str]`, `question: str`
  - `class FollowUp(BaseModel)`: `path: str`, `question: str`, `current: Any`
  - `class FrameworkRun(BaseModel)`: `mode: Literal["live", "replay", "fixture"]`, `map_reviewed: bool`, `success_end: str | None`, `doc_gate: dict[str, Answer]`, `gate_score: float = 0`, `gate_band: Literal["High", "Medium", "Low"] = "Low"`, `context: dict[str, Answer]`, `map_checks: list[MapCheck] = []`, `readiness_defaults: dict[str, Answer]`, `steps: dict[str, StepAssessment]`, `redesign_potential: int = 1`, `posture: list[str] = []`, `waves: dict[int, list[str]] = {}`, `follow_ups: list[FollowUp] = []`, `summary_text: str = ""`, `first_actions: list[str] = []`, `trace: list[AgentStep] = []`
  - Answer paths are dotted strings: `"doc_gate.8"`, `"context.volume"`, `"readiness_defaults.people"`, `"steps.A2.dials.stakes"`, `"steps.A2.r_pass1.R4"`, `"steps.A2.readiness.bus_factor"`.
  - `get_answer(run: FrameworkRun, path: str) -> Answer` (raises `KeyError`)
  - `allowed_values(path: str) -> tuple` (by the key's family: R → `YNU`, D/gate → `YPN`, A → `YN`, dials/readiness → `SCORE`, context/worksheet → `None` meaning free text or a number)

- [ ] **Step 1: Write the failing tests**

```python
def test_stated_without_quote_becomes_assumed():
    a = Answer(value="Y", basis="stated")
    assert a.basis == "assumed" and "no quote" in a.flags

def test_derived_stated_needs_no_quote():
    assert Answer(value="Y", basis="stated", derived=True).basis == "stated"

def test_assumed_helper():
    a = assumed(3, "How varied are the inputs?")
    assert (a.value, a.basis, a.confidence, a.question) == (3, "assumed", "L", "How varied are the inputs?")

def test_get_answer_and_allowed_values(minimal_run):
    assert get_answer(minimal_run, "steps.A2.dials.stakes").value == 3
    assert allowed_values("steps.A2.dials.stakes") == SCORE
    assert allowed_values("steps.A2.r_pass1.R4") == YNU
    assert allowed_values("doc_gate.8") == YPN
    with pytest.raises(KeyError):
        get_answer(minimal_run, "steps.ZZ.dials.stakes")
```

Add a `minimal_run` fixture to `conftest.py`: a `FrameworkRun` with one step `A2` whose six dials are `assumed(3, …)`.

- [ ] **Step 2: Run** `pytest tests/test_models.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `framework/models.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: framework data models and answer paths`.

---

### Task 4: Graph facts

**Files:**
- Create: `framework/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `ProcessModel`.
- Produces:
  - `@dataclass class GraphFacts`: `steps: list[str]` (tasks, subprocesses and decisions in `order`, no start/end), `succ: dict[str, set[str]]`, `pred: dict[str, set[str]]`, `actor: dict[str, str]` (role id), `same_actor_neighbours: dict[str, set[str]]` (directly connected steps in the same lane), `start_ids: list[str]`, `end_ids: list[str]`, `success_end: str | None`, `main_path: set[str]`
  - `graph_facts(model: ProcessModel, success_end: str | None = None) -> GraphFacts`. Edges come from `model.edges` plus decision branches. `success_end=None` → `propose_success_end(model)`.
  - `propose_success_end(model) -> str | None`: the end event with the greatest shortest-path distance (BFS) from a start event; ties go to the later one in `model.activities`; `None` when no end is reachable.
  - `main_path` = steps reachable from a start **and** able to reach `success_end` (empty when `success_end` is `None`).

- [ ] **Step 1: Write the failing tests**

```python
def test_graph_facts_demo(demo_model):
    f = graph_facts(demo_model)
    assert "S0" not in f.steps and "E1" not in f.steps and "D1" in f.steps
    assert f.success_end in f.end_ids
    assert "A1" in f.main_path
    assert f.actor["A2"] == next(a.role_id for a in demo_model.activities if a.id == "A2")

def test_success_end_override(demo_model):
    assert graph_facts(demo_model, success_end="E1").success_end == "E1"

def test_graph_facts_edge_cases(demo_model):
    m = demo_model.model_copy(deep=True)
    m.edges = []; [setattr(d, "branches", []) for d in m.decisions]   # nothing connected
    f = graph_facts(m)
    assert f.success_end is None and f.main_path == set()
    one_lane = demo_model.model_copy(deep=True)
    for a in one_lane.activities: a.role_id = one_lane.roles[0].id
    for d in one_lane.decisions: d.role_id = one_lane.roles[0].id
    assert graph_facts(one_lane).same_actor_neighbours["A1"]   # everything is same-actor now
```

- [ ] **Step 2: Run** `pytest tests/test_graph.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `framework/graph.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: graph facts for framework rules`.

---

### Task 5: Part 0 rules (documentation gate)

**Files:**
- Create: `framework/rules.py`
- Test: `tests/test_rules_gate.py`

**Interfaces:**
- Consumes: `Settings`, `GraphFacts`, `Answer`.
- Produces:
  - `auto_gate_checks(model: ProcessModel, facts: GraphFacts, names: list[str]) -> dict[str, Answer]` for keys `"1", "2", "4", "5", "6", "7"`, each `basis="stated", derived=True` with a one-line reason:
    - 1: Y if ≥ 1 start and ≥ 1 end event, else N.
    - 2: N if any role name contains a name from `names` (case-insensitive), else Y.
    - 4: share of decisions whose question ends with "?" and whose branches all have labels: 100% Y, ≥ 50% Partly, else N. No decisions → Y.
    - 5: Y if `validate_graph()` is empty and `success_end` is not None; Partly if the graph is valid but no end is reachable; N otherwise.
    - 6: share of `kind == "activity"` steps with non-empty `tools`: 100% Y, ≥ 50% Partly, else N.
    - 7: subprocesses each with a role and a non-empty description: all → Y (or none exist); some → Partly; none → N.
  - `score_gate(answers: dict[str, Answer], s: Settings) -> tuple[float, str]` (Y = 1, Partly = 0.5, anything else 0; band "High" if ≥ `gate.high`, "Medium" if ≥ `gate.medium`, else "Low").

- [ ] **Step 1: Write the failing tests**

```python
def test_auto_gate_checks_demo(demo_model):
    g = auto_gate_checks(demo_model, graph_facts(demo_model), names=["Liz", "Stephan"])
    assert set(g) == {"1", "2", "4", "5", "6", "7"}
    assert g["1"].value == "Y" and g["2"].value == "Y" and g["4"].value == "Y"
    assert g["6"].value in ("Partly", "N") and all(a.derived for a in g.values())

def test_gate_role_with_name(demo_model):
    m = demo_model.model_copy(deep=True); m.roles[0].name = "Liz"
    assert auto_gate_checks(m, graph_facts(m), names=["Liz"])["2"].value == "N"

@pytest.mark.parametrize("ys,partlies,band", [(8, 0, "High"), (7, 0, "High"), (6, 1, "Medium"), (4, 0, "Medium"), (3, 1, "Low")])
def test_score_gate_bands(ys, partlies, band, settings):
    vals = ["Y"] * ys + ["Partly"] * partlies + ["N"] * (8 - ys - partlies)
    answers = {str(i + 1): Answer(value=v, basis="stated", derived=True) for i, v in enumerate(vals)}
    assert score_gate(answers, settings) == (ys + 0.5 * partlies, band)
```

(Add `settings` fixture → `load_settings()` to `conftest.py`.)

- [ ] **Step 2: Run** `pytest tests/test_rules_gate.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in `framework/rules.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Part 0 documentation gate rules`.

---

### Task 6: Part 2 rules (should it exist)

**Files:**
- Modify: `framework/rules.py`
- Test: `tests/test_rules_part2.py`

**Interfaces:**
- Produces:
  - `yes(a: Answer | None) -> bool` (True only for value "Y"; "Unknown", "N", None → False)
  - `part2_call(step: StepAssessment, facts: GraphFacts, is_decision: bool) -> tuple[Call, str | None]` returning `(call, combine_target)`. Uses `r_pass2[k]` when present, else `r_pass1[k]`. First match in R1…R7 order:
    - R1 → Remove; R2 → Remove; R3 → Combine (target = `combine_with.value` if it's a step id, else None); R4 → `r_move.value` if it is "Move", else Remove; R5 → Remove; R6 → Combine only if `combine_with.value` ∈ `facts.same_actor_neighbours[step_id]`, otherwise skip R6; R7 → Fix the map; none → Keep.
    - Protection: if `is_decision` or `yes(step.accountability)`, a Remove/Combine/Move result becomes Keep, **unless the matching rule is R2**. Sets `step.protected`.
  - `redesign_potential(calls: list[Call], s: Settings) -> int` (percent of non-Keep; < bands[0] → 1, < bands[1] → 2, < bands[2] → 3, < bands[3] → 4, else 5; empty list → 1).

- [ ] **Step 1: Write the failing tests**

```python
def _step(sid="A5", **r):
    return StepAssessment(step_id=sid, worksheet={}, r_pass1={k: Answer(value=r.get(k, "N"), basis="stated", derived=True) for k in R_KEYS})

def test_first_match_wins(demo_facts):
    assert part2_call(_step(R2="Y", R7="Y"), demo_facts, False)[0] == "Remove"

def test_unknown_counts_as_no(demo_facts):
    assert part2_call(_step(R1="Unknown"), demo_facts, False)[0] == "Keep"

def test_decision_protected_except_r2(demo_facts):
    assert part2_call(_step("D3", R1="Y"), demo_facts, True)[0] == "Keep"
    assert part2_call(_step("D1", R2="Y"), demo_facts, True)[0] == "Remove"

def test_r4_move_or_remove(demo_facts):
    s = _step(R4="Y"); s.r_move = Answer(value="Move", basis="stated", derived=True)
    assert part2_call(s, demo_facts, False)[0] == "Move"

def test_r6_needs_same_actor_neighbour(demo_facts):
    s = _step("A1", R6="Y"); s.combine_with = Answer(value="A24", basis="stated", derived=True)  # not adjacent
    assert part2_call(s, demo_facts, False)[0] == "Keep"

def test_pass2_overrides_pass1(demo_facts):
    s = _step(R1="Y"); s.r_pass2 = {"R1": Answer(value="N", basis="stated", derived=True)}
    assert part2_call(s, demo_facts, False)[0] == "Keep"

@pytest.mark.parametrize("non_keep,total,score", [(0, 10, 1), (1, 10, 2), (3, 10, 3), (4, 10, 4), (6, 10, 5), (0, 0, 1)])
def test_redesign_potential(non_keep, total, score, settings):
    assert redesign_potential(["Remove"] * non_keep + ["Keep"] * (total - non_keep), settings) == score
```

(Add `demo_facts` fixture → `graph_facts(demo_model)`.)

- [ ] **Step 2: Run** `pytest tests/test_rules_part2.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Part 2 should-it-exist rules`.

---

### Task 7: Part 3 rules (tiers)

**Files:**
- Modify: `framework/rules.py`
- Test: `tests/test_rules_tiers.py`

**Interfaces:**
- Produces:
  - `dial(step: StepAssessment, key: str, s: Settings) -> int`: the int value if it's in 1–5, else the default (`defaults.rule_clarity` for `rule_clarity`, `defaults.dial` otherwise).
  - `agent_test_pass(at: dict[str, Answer]) -> bool` = `(yes(A1) or yes(A2)) and not yes(A3) and yes(A4)`.
  - `tier_decision(step: StepAssessment, s: Settings) -> TierResult`. `standardize_first` = not all of D1–D3 are "Y". Then first match (rule numbers as in the starter, recorded in `rule_fired`):
    1. stakes ≥ `tier.human.stakes` → Human
    2. clarity ≥ `tier.rules.clarity_min`, input ≥ `input_min`, variation ≤ `variation_max`, judgment ≤ `judgment_max` → Rules
    3. checkability ≥ `tier.agent.checkability_min` and (judgment ≥ `judgment_min` or input ≤ `input_max` or variation ≥ `variation_min`) → Agent if `agent_test_pass` (`approval` = stakes ≥ `approval_stakes_min`, `error_budget = True`), else Rules with `human_task = True`
    4. judgment ≥ `tier.human.judgment_min` and checkability ≤ `checkability_max` → Human
    5. otherwise → Mixed

- [ ] **Step 1: Write the failing tests** (table-driven):

```python
def _t(d=("Y", "Y", "Y"), a=("Y", "N", "N", "Y"), **dials):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={})
    st.tier1 = {k: Answer(value=v, basis="stated", derived=True) for k, v in zip(D_KEYS, d)}
    st.agent_test = {k: Answer(value=v, basis="stated", derived=True) for k, v in zip(A_KEYS, a)}
    st.dials = {k: Answer(value=v, basis="stated", derived=True) for k, v in dials.items()}
    return st

CASES = [
    (dict(stakes=5, rule_clarity=5, input_structure=5, variation=1, judgment=1, checkability=5), ("Human", 1, False)),
    (dict(stakes=2, rule_clarity=4, input_structure=4, variation=2, judgment=1, checkability=5), ("Rules", 2, False)),
    (dict(stakes=3, rule_clarity=2, input_structure=2, variation=3, judgment=3, checkability=4), ("Agent", 3, True)),
    (dict(stakes=2, rule_clarity=2, input_structure=3, variation=2, judgment=4, checkability=2), ("Human", 4, False)),
    (dict(stakes=2, rule_clarity=3, input_structure=3, variation=2, judgment=2, checkability=2), ("Mixed", 5, False)),
]

@pytest.mark.parametrize("dials,expected", CASES)
def test_tier_decision(dials, expected, settings):
    r = tier_decision(_t(**dials), settings)
    assert (r.tier, r.rule_fired, r.approval) == expected

def test_agent_test_fail_gives_rules_plus_human_task(settings):
    r = tier_decision(_t(a=("Y", "N", "Y", "Y"), stakes=2, rule_clarity=2, input_structure=2, variation=3, judgment=3, checkability=4), settings)
    assert (r.tier, r.human_task) == ("Rules", True)

def test_standardize_first(settings):
    assert tier_decision(_t(d=("Y", "Partly", "Y"), stakes=2, rule_clarity=4, input_structure=4, variation=1, judgment=1, checkability=5), settings).standardize_first

def test_dial_defaults(settings):
    st = _t()
    assert dial(st, "rule_clarity", settings) == 1 and dial(st, "variation", settings) == 3
    st.dials["variation"] = Answer(value=9, basis="stated", derived=True)
    assert dial(st, "variation", settings) == 3
```

- [ ] **Step 2: Run** `pytest tests/test_rules_tiers.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Part 3 tier rules`.

---

### Task 8: Parts 4–5 rules and the full code pass

**Files:**
- Modify: `framework/rules.py`
- Test: `tests/test_rules_recommend.py`

**Interfaces:**
- Produces:
  - `readiness_min(step: StepAssessment, defaults: dict[str, Answer], s: Settings) -> int`: per key, the step override if present else the process default; invalid or missing → `defaults.readiness`; returns the minimum.
  - `recommend(step: StepAssessment, s: Settings) -> Recommendation`:
    - call is Fix the map → action "Ask the owner, then rescore", wave 0
    - call is Remove / Combine / Move / Reimagine → action named after the call, wave 0
    - otherwise: `standardize_first` → wave 1 (and the tier's wave is still listed in the action, e.g. "Standardize first, then Rules"); tier Rules → wave 2; Agent → wave 3; Human / Mixed → wave None
    - `harden_first` = tier in (Rules, Agent) and `readiness_min < readiness.ready_min`
    - default `chain`: Human → [Human task]; Rules → [Rules] (+ Human task if `human_task`); Agent → [Agent] (+ Human approval if `approval`). `chain` is replaced later by the AI specs step when present.
  - `posture(run: FrameworkRun, facts: GraphFacts) -> list[str]`, first true line wins, then "Harden first" appended if any automated step has `harden_first`: gate_band Low → "Document first"; any `map_checks` touching `facts.main_path` → "Fix the map"; redesign 4–5 → "Redesign first"; 3 → "Redesign the flagged segments, then standardize and automate the rest"; 1–2 → "Incremental".
  - `waves(run) -> dict[int, list[str]]` (keys 0–3; step ids; a step appears in its earliest wave only).
  - `follow_ups(run) -> list[FollowUp]`: every `Answer` with `basis == "assumed"`, process-level paths first (doc_gate, context, readiness_defaults), then by step in `facts.steps` order.
  - `apply_rules(run: FrameworkRun, model: ProcessModel, s: Settings, names: list[str] = []) -> FrameworkRun`: the whole code pass, in order: graph facts → auto gate checks merged into `doc_gate` (never overwriting an `answered` value) → `score_gate` → Medium: add flag `"doc confidence Medium"` and cap confidence at M on every non-`answered` step answer → single-source cap: on steps backed by fewer than 2 source files (`ingest.merge.source_coverage`), cap the confidence of every `stated`, non-`derived` answer at M → Part 2 calls → redesign potential → if band is Low, stop scoring tiers (tier/readiness/recommendation left None) → tiers → readiness → recommendations → "Write the SOP" appended to the action of Rules/Agent steps whose doc_gate check 8 is not Y → posture → waves → follow-ups. Pure function (returns a new run).

- [ ] **Step 1: Write the failing tests**

```python
def test_readiness_min_uses_lowest_and_defaults(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, readiness={"bus_factor": Answer(value=2, basis="stated", derived=True)})
    defaults = {k: Answer(value=5, basis="stated", derived=True) for k in READINESS_KEYS}
    assert readiness_min(st, defaults, settings) == 2
    assert readiness_min(StepAssessment(step_id="Y", worksheet={}, r_pass1={}), {}, settings) == 1

def test_recommend_waves(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, call="Keep", readiness_min=4,
                        tier=TierResult(standardize_first=True, tier="Rules", rule_fired=2))
    r = recommend(st, settings)
    assert r.wave == 1 and "Rules" in r.action and not r.harden_first and [c.type for c in r.chain] == ["Rules"]
    st.call = "Remove"; assert recommend(st, settings).wave == 0

def test_harden_first_when_not_ready(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, call="Keep", readiness_min=2,
                        tier=TierResult(standardize_first=False, tier="Agent", approval=True, rule_fired=3))
    r = recommend(st, settings)
    assert r.harden_first and [c.type for c in r.chain] == ["Agent", "Human approval"]

def test_apply_rules_low_gate_stops_tiers(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    for k in GATE_KEYS: run.doc_gate[k] = Answer(value="N", basis="answered")
    out = apply_rules(run, demo_model, settings)
    assert out.gate_band == "Low" and out.posture[0] == "Document first"
    assert all(st.tier is None for st in out.steps.values())

def test_apply_rules_never_overwrites_answered(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True); run.doc_gate["6"] = Answer(value="Y", basis="answered")
    assert apply_rules(run, demo_model, settings).doc_gate["6"].value == "Y"

def test_single_source_caps_confidence(demo_model, all_assumed_run, settings):
    cov = source_coverage(demo_model)
    single = next(s for s in all_assumed_run.steps if cov.get(s, 0) < 2)
    run = all_assumed_run.model_copy(deep=True)
    run.steps[single].dials["stakes"] = Answer(value=4, basis="stated", confidence="H", quotes=[SourceRef(file="f", location="1", quote="q")])
    assert apply_rules(run, demo_model, settings).steps[single].dials["stakes"].confidence == "M"

def test_follow_ups_list_every_assumed(demo_model, all_assumed_run, settings):
    out = apply_rules(all_assumed_run, demo_model, settings)
    assert out.follow_ups and out.follow_ups[0].path.split(".")[0] in ("doc_gate", "context", "readiness_defaults")
    assert all(get_answer(out, f.path).basis == "assumed" for f in out.follow_ups)
```

Add fixture `all_assumed_run(demo_model)`: a `FrameworkRun` for the demo where every input in every family is `assumed(default, question)` (this is also what Task 9 produces for steps the AI omits, so build it with a helper `blank_run(model, mode) -> FrameworkRun` that you put in `framework/models.py` and reuse in Task 9).

- [ ] **Step 2: Run** `pytest tests/test_rules_recommend.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions and `blank_run`.
- [ ] **Step 4: Run** `pytest -v` (all tests). Expected: PASS.
- [ ] **Step 5: Commit** `feat: Parts 4-5 rules and full code pass`.

---

### Task 9: AI steps and the pipeline

**Files:**
- Create: `framework/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `ingest.agent._call`, `ingest.agent._json_with_repair`, `ingest.agent.REVIEW_MODEL`, `ingest.extract.MODEL`, `ingest.parse.to_prompt_text`, `blank_run`, `apply_rules`, `graph_facts`.
- Produces:
  - `parse_answers(raw: dict, keys: list[str], allowed: tuple | None, defaults: dict[str, Any], questions: dict[str, str]) -> dict[str, Answer]`: each raw item `{"value", "basis", "reason", "confidence", "quotes"}`; a missing key or a value outside `allowed` → `assumed(default, question)` with flag `"invalid AI value"` when it was present but invalid. Basis from the AI may only be "stated" or "assumed" ("answered" from the AI → "assumed").
  - `apply_verifier(run: FrameworkRun, findings: list[dict]) -> FrameworkRun`: each finding `{"path", "action": "downgrade" | "lower_confidence", "reason"}`. "downgrade" sets the answer to the framework default with `basis="assumed"` and keeps the old value in `history`; "lower_confidence" moves H→M or M→L. Any other action, an unknown path, or a path whose basis is `answered` is ignored.
  - `stage1_context(model: ProcessModel, agent_run: AgentRun | None) -> str`: the Stage 1 evidence block every AI prompt gets after the map, per the spec's "What Stage 1 feeds in" table. Sections, in order: `PAIN POINTS` (id, step id + name, category, frequency, quotes, and the hint line for its category: delay → R4, manual → R3, rework → R5, ownership → R2, missing-rule → R7 / Rule clarity), `OPEN QUESTIONS`, `SOURCE DISAGREEMENTS` (discrepancies with what each source says, plus reconciliation items of kind conflict/single-source), `REJECTED VERIFIER FINDINGS`, `HANDOFFS`, `DEPENDENCIES`, `STEP DETAIL` (per step: tools with CGS tag, minutes, purpose, control_now, judgment_reason_stated, step_type, notes, description, assumptions, source count). Empty sections are left out. With `agent_run=None` the run-only sections are left out.
  - `run_framework(model: ProcessModel, parsed: list[ParsedFile], *, agent_run: AgentRun | None = None, map_reviewed: bool, success_end: str | None = None, force_live: bool = False, progress: Callable[[AgentStep], None] | None = None, s: Settings | None = None, names: list[str] = []) -> FrameworkRun`
- Pipeline (spec section 8), each step recorded as an `AgentStep` with its Phase 3 label as `agent` and the output JSON kept in the trace's `artifact` key (store artifacts in a `dict` on the run under `trace` step ids; add `artifacts: dict[str, Any] = {}` to `FrameworkRun`):
  1. Load (code) · 2. Doc gate: AI judges checks 3 and 8 only · 3. Context reader (Toil Task Assessor): context answers, map checks, worksheet per step · 4. Step Removal pass 1: R1–R7, `r_move`, `accountability`, `combine_with` per step · 5. Step Removal pass 2 (context): only changed answers · 6. Disambiguation: D1–D3, dials, A1–A4 for steps not removed by the code pass after 5 · 6b. Step Removal loop: changed R answers given tier results, plus `made_unnecessary_by` · 7. Readiness: process defaults + per-step overrides · 8. Verifier (review model) · 9. Recommend (code, `apply_rules`) · 10. Workflow Documentation & Specification: chain + spec per new step, for steps with a recommendation · 11. Summary: `summary_text` (context paragraph) and `first_actions` (3) written only from the computed results.
  - `apply_rules` runs after steps 4, 5, 6, 6b and 8 (each AI step needs the current calls).
  - The loop runs `s.loop.extra_passes` times (1).
  - Prompts are built from `docs/cgs-framework-starter.md` wording for the questions asked. Every prompt says: answer the questions only; never decide calls, tiers or scores; give a quote for every "stated" answer; use "Unknown"/"assumed" when the materials don't say. The whole process goes in one call per step; split by lane only on `Truncated`.
  - Cache: `.cache/framework_<hash>.json`, hash = sha256 of `model.model_dump_json()` + chunk texts. Replay: `demo_data/framework_run_sales_process.json` when the hash matches the demo's (written by Task 15's `--record`). No cache, no replay and no API key → `RuntimeError("No API key and no saved run for this map. Set ANTHROPIC_API_KEY in .env.")`.

- [ ] **Step 1: Write the failing tests** (no network: monkeypatch `framework.agent._call` with a fake that returns canned JSON keyed by which prompt it received):

```python
def test_parse_answers_tolerates_bad_ai_output():
    raw = {"R1": {"value": "Probably", "basis": "stated", "quotes": []}, "R2": {"value": "Y", "basis": "answered", "quotes": [{"file": "a", "location": "1", "quote": "q"}]}}
    out = parse_answers(raw, R_KEYS, YNU, {k: "Unknown" for k in R_KEYS}, {k: f"{k}?" for k in R_KEYS})
    assert out["R1"].basis == "assumed" and "invalid AI value" in out["R1"].flags
    assert out["R2"].basis == "assumed"            # AI can't claim "answered"
    assert out["R7"].value == "Unknown" and out["R7"].question == "R7?"

def test_apply_verifier_only_downgrades(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    run.steps["A6"].dials["stakes"] = Answer(value=4, basis="stated", confidence="H", quotes=[SourceRef(file="f", location="1", quote="q")])
    run.steps["A6"].dials["judgment"] = Answer(value=2, basis="answered")
    out = apply_verifier(run, [
        {"path": "steps.A6.dials.stakes", "action": "downgrade", "reason": "quote doesn't say"},
        {"path": "steps.A6.dials.judgment", "action": "downgrade", "reason": "x"},
        {"path": "steps.A6.dials.variation", "action": "raise", "reason": "x"},
        {"path": "steps.NOPE.dials.stakes", "action": "downgrade", "reason": "x"}])
    s = out.steps["A6"].dials
    assert (s["stakes"].basis, s["stakes"].value) == ("assumed", 3) and s["stakes"].history
    assert s["judgment"].value == 2

def test_run_framework_with_fake_ai(monkeypatch, demo_model, settings):
    monkeypatch.setattr("framework.agent._call", fake_call_from("tests/fixtures/fake_ai_responses.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    assert [t.agent for t in run.trace][:4] == ["Orchestrator", "Documentation gate", "Toil Task Assessor", "Step Removal"]
    assert set(run.steps) == set(graph_facts(demo_model).steps)    # omitted steps still assessed (all assumed)
    assert run.follow_ups

def test_stage1_context_includes_pain_points_and_run(demo_model):
    run = AgentRun.model_validate_json((ROOT / "demo_data/agent_run_sales_process.json").read_text())
    ctx = stage1_context(demo_model, run)
    for heading in ("PAIN POINTS", "OPEN QUESTIONS", "SOURCE DISAGREEMENTS", "STEP DETAIL"):
        assert heading in ctx
    p = demo_model.pain_points[0]
    assert p.id in ctx and p.description[:30] in ctx
    assert "SOURCE DISAGREEMENTS" not in stage1_context(demo_model, None)

def test_every_ai_prompt_gets_stage1_context(monkeypatch, demo_model, settings):
    prompts = []
    fake = fake_call_from("tests/fixtures/fake_ai_responses.json")
    monkeypatch.setattr("framework.agent._call", lambda system, user, **k: prompts.append(user) or fake(system, user, **k))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    judged = [u for u in prompts if not u.startswith(("[STEP:verify]", "[STEP:summary]"))]
    assert judged and all("PAIN POINTS" in u for u in judged)

def test_run_framework_without_key_or_cache(monkeypatch, demo_model, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("framework.agent.CACHE_DIR", tmp_path)
    m = demo_model.model_copy(deep=True); m.process.name = "Something else"
    with pytest.raises(RuntimeError, match="No API key"):
        run_framework(m, [], map_reviewed=False)
```

`tests/fixtures/fake_ai_responses.json`: one small canned response per AI step. Include one unknown step id ("ZZ9") and omit at least one real step, so the tolerance is exercised end to end. `fake_call_from(path)` lives in `tests/conftest.py`: it picks the response by a marker string each prompt starts with (`[STEP:gate]`, `[STEP:context]`, `[STEP:part2]`, `[STEP:part2_context]`, `[STEP:tiers]`, `[STEP:loop]`, `[STEP:readiness]`, `[STEP:verify]`, `[STEP:specs]`, `[STEP:summary]`). Prompts must start with these markers.

- [ ] **Step 2: Run** `pytest tests/test_agent.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `framework/agent.py`.
- [ ] **Step 4: Run** `pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Phase 3 AI steps and pipeline`.

---

### Task 10: Answering follow-ups

**Files:**
- Modify: `framework/rules.py`
- Test: `tests/test_rescore.py`

**Interfaces:**
- Produces: `answer_and_rescore(run: FrameworkRun, model: ProcessModel, path: str, value: Any, s: Settings, reason: str = "Answered in the app") -> FrameworkRun`. Validates `value` against `allowed_values(path)` (numbers given as strings are converted for SCORE paths); invalid → `ValueError("<value> isn't allowed here. Choose one of: …")` with the run unchanged. Valid → the answer becomes `basis="answered"`, `confidence="H"`, the old answer is pushed to `history`, then `apply_rules`. No AI call. Also `undo_answer(run, model, path, s) -> FrameworkRun` restores the last history entry.

- [ ] **Step 1: Write the failing tests**

```python
def test_answer_rescores_without_ai(monkeypatch, demo_model, all_assumed_run, settings):
    monkeypatch.setattr("framework.agent._call", lambda *a, **k: pytest.fail("AI called"))
    run = apply_rules(all_assumed_run, demo_model, settings)
    out = answer_and_rescore(run, demo_model, "steps.A11.dials.stakes", "5", settings)
    assert out.steps["A11"].dials["stakes"].basis == "answered" and out.steps["A11"].tier.tier == "Human"
    assert "steps.A11.dials.stakes" not in [f.path for f in out.follow_ups]

def test_answer_follow_up_rejects_invalid_value(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    for path, bad in [("steps.A11.dials.stakes", "10"), ("steps.A11.r_pass1.R1", "yes please")]:
        with pytest.raises(ValueError):
            answer_and_rescore(run, demo_model, path, bad, settings)
    assert run.steps["A11"].dials["stakes"].basis == "assumed"

def test_undo_answer(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    out = undo_answer(answer_and_rescore(run, demo_model, "context.volume", "40 leads a month", settings), demo_model, "context.volume", settings)
    assert out.context["volume"].basis == "assumed"
```

- [ ] **Step 2: Run** `pytest tests/test_rescore.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: answer follow-ups and rescore without AI`.

---

### Task 11: Future-state model

**Files:**
- Create: `framework/future.py`
- Test: `tests/test_future.py`

**Interfaces:**
- Produces: `AUTOMATION_ROLE_ID = "R_AUTO"`; `build_future_model(model: ProcessModel, run: FrameworkRun, show_removed: bool = False) -> tuple[ProcessModel, dict[str, str]]`, returning the future model and `markers: {node_id: "rules" | "agent" | "approval" | "task" | "removed"}`.
- Rules:
  - **Remove** (and `made_unnecessary_by` set): drop the node. Incoming edges and branches are re-pointed to its replacement: for an activity, its single successor (if several, the one on `main_path`, else the first); for a decision, the target of the branch that lies on `main_path`, else the branch labelled "Yes", else the first branch. Repeat until the replacement is a kept node (handles chains of removed steps). With `show_removed=True`, keep the node with marker "removed" and no re-pointing.
  - **Combine**: drop the node, re-point to `combine_target` (or treat as Remove if that's None), and merge its `source_refs` and `tools` into the survivor.
  - **Move / Reimagine / Fix the map**: keep the node unchanged (no marker); the step table carries the note.
  - **Keep with a chain**: replace the node with one node per `ChainStep` (ids `<id>.1`, `<id>.2`, …) linked in order. The first inherits incoming edges, the last inherits outgoing edges and branches. Rules/Agent nodes go in role `R_AUTO` ("Automation", added to `roles` only if used); Human approval/Human task go in `chain.role_id` or the original lane. Marker by type. Decisions are kept as decisions (no chain) with no marker.
  - `process.name` unchanged; the caller passes the title "— future state".
  - Pain points pointing at dropped nodes are dropped. `validate_graph()` must be empty.

- [ ] **Step 1: Write the failing tests**

```python
def test_future_drops_removed_and_types_chains(demo_model, acceptance_run):
    fut, markers = build_future_model(demo_model, acceptance_run)
    ids = fut.node_ids()
    assert "D1" not in ids and fut.validate_graph() == []
    assert markers["A6.1"] == "agent" and markers["A6.2"] == "approval"
    assert next(a for a in fut.activities if a.id == "A6.1").role_id == AUTOMATION_ROLE_ID

def test_remove_chain_of_steps_keeps_graph_valid(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    for sid in ("A2", "A3", "A4", "D1"): run.steps[sid].call = "Remove"
    fut, _ = build_future_model(demo_model, run)
    assert fut.validate_graph() == [] and not {"A2", "A3", "A4", "D1"} & fut.node_ids()

def test_show_removed_keeps_greyed_nodes(demo_model, acceptance_run):
    fut, markers = build_future_model(demo_model, acceptance_run, show_removed=True)
    assert markers["D1"] == "removed" and "D1" in fut.node_ids()
```

`acceptance_run` fixture = `apply_rules` over `tests/fixtures/demo_answers.json` loaded into `blank_run` (created in Task 14; until then, create the fixture file with just the entries for D1, D5, D8, A2, A6 and A11 given in Task 14 and leave everything else assumed).

- [ ] **Step 2: Run** `pytest tests/test_future.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: build future-state process model`.

---

### Task 12: Step-type markers in the renderers

**Files:**
- Modify: `render/swimlane.py` (`render_svg`, legend), `render/drawio.py` (`render_drawio`)
- Test: `tests/test_render_markers.py`

**Interfaces:**
- Produces: `render_svg(..., markers: dict[str, str] | None = None)` and `render_drawio(model, g, markers: dict[str, str] | None = None)`. Existing calls (no `markers`) render exactly as before.
- Black and white only: "rules" = task with a small gear glyph (⚙) at the top-left inside the box; "agent" = task with a double border; "approval" = task with a check glyph (✓) at the top-left; "task" = plain task; "removed" = grey dashed outline and grey text. When `markers` is given, the legend adds the four step types (and "removed" if present). In draw.io, "agent" uses `shape=doubleRect` (or `double=1`), the glyphs are in the label, and "removed" uses `dashed=1;strokeColor=#9CA3AF;fontColor=#9CA3AF`.

- [ ] **Step 1: Write the failing tests**

```python
def test_markers_render(demo_model):
    g = build_geometry(demo_model)
    svg = render_svg(demo_model, geometry=g, markers={"A2": "rules", "A6": "agent", "A11": "approval", "D1": "removed"})
    assert "⚙" in svg and "✓" in svg and "Agent" in svg   # legend
    xml = render_drawio(demo_model, g, markers={"A6": "agent", "D1": "removed"})
    assert "dashed=1" in xml and ("doubleRect" in xml or "double=1" in xml)

def test_no_markers_unchanged(demo_model):
    g = build_geometry(demo_model)
    assert render_svg(demo_model, geometry=g) == render_svg(demo_model, geometry=g, markers=None)
    assert "⚙" not in render_svg(demo_model, geometry=g)
```

- [ ] **Step 2: Run** `pytest tests/test_render_markers.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS. Open the SVG from the future model in a browser and check the glyphs are inside the boxes and don't collide with labels.
- [ ] **Step 5: Commit** `feat: step-type markers in swimlane and draw.io`.

---

### Task 13: Outputs (step table, summary, follow-ups)

**Files:**
- Create: `framework/outputs.py`
- Test: `tests/test_outputs.py`

**Interfaces:**
- Produces:
  - `STEP_TABLE_COLUMNS = ["step_id", "step", "lane", "call", "tier_result", "future_chain", "wave", "readiness", "reason", "confidence", "gaps"]`
  - `step_table(run: FrameworkRun, model: ProcessModel) -> list[dict]` (one row per `facts.steps` entry; `tier_result` like "Standardize first → Rules" or "Agent + Human approval"; `future_chain` like "Agent → Human approval"; `reason` = the reason of the answer that decided the call (or the tier rule when Keep); `confidence` = the lowest confidence among the answers that decided it; `gaps` = count of `assumed` answers on the step)
  - `step_table_csv(rows: list[dict]) -> str`
  - `summary_markdown(run: FrameworkRun, model: ProcessModel) -> str` with headings in this order: Outcome and goal, Documentation confidence, Open map questions, Redesign potential, Steps by tier, Readiness by test, Posture, Waves, First three actions, Gaps. Every number comes from `run`; any missing fact is written as "Gap: <what's missing>".
  - `follow_ups_markdown(run: FrameworkRun, model: ProcessModel) -> str` (process facts first, then by step name, each question once).
  - All three text outputs pass through the name safety net (`handoff.speaker_names` + `with_nicknames`, names given by the caller as `names: list[str] = []`).

- [ ] **Step 1: Write the failing tests**

```python
def test_step_table_rows(demo_model, acceptance_run):
    rows = step_table(acceptance_run, demo_model)
    assert list(rows[0]) == STEP_TABLE_COLUMNS
    a6 = next(r for r in rows if r["step_id"] == "A6")
    assert a6["call"] == "Keep" and a6["tier_result"] == "Agent + Human approval" and a6["future_chain"] == "Agent → Human approval"
    assert step_table_csv(rows).splitlines()[0] == ",".join(STEP_TABLE_COLUMNS)

def test_summary_sections_and_gaps(demo_model, all_assumed_run, settings):
    md = summary_markdown(apply_rules(all_assumed_run, demo_model, settings), demo_model)
    heads = [l[3:] for l in md.splitlines() if l.startswith("## ")]
    assert heads == ["Outcome and goal", "Documentation confidence", "Open map questions", "Redesign potential",
                     "Steps by tier", "Readiness by test", "Posture", "Waves", "First three actions", "Gaps"]
    assert "Gap:" in md

def test_names_removed(demo_model, acceptance_run):
    acceptance_run.summary_text = "Liz reviews it"
    assert "Liz" not in summary_markdown(acceptance_run, demo_model, names=["Liz"])
```

- [ ] **Step 2: Run** `pytest tests/test_outputs.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: step table, summary and follow-up exports`.

---

### Task 14: Acceptance test on CGS's example

**Files:**
- Create/complete: `tests/fixtures/demo_answers.json`
- Test: `tests/test_acceptance.py`

**Interfaces:**
- Consumes: `blank_run`, `apply_rules`, `build_future_model`. Add `load_answers(run: FrameworkRun, path: Path) -> FrameworkRun` to `framework/models.py`: the JSON maps answer paths to Answer dicts (`{"steps.D1.r_pass1.R2": {"value": "Y", "basis": "stated", "reason": "...", "confidence": "H", "quotes": [...]}}`); every path not listed stays assumed.
- Curated answers (all `stated` with a real quote from the demo transcripts, chosen to reflect what the transcripts say):
  - D1, D5, D8: `R2 = Y` (exists only because Insightly logins differ).
  - A3, A4, A13, A14, A23, A24 (the request-and-enter paths behind the access checks): `R2 = Y`.
  - A2: R1–R7 = N; D1 = Y, D2 = Partly, D3 = Y; dials rule_clarity 4, input_structure 4, variation 2, judgment 1, checkability 5, stakes 2; readiness bus_factor 2.
  - A6: R1–R7 = N; D1–D3 = Y; dials rule_clarity 2, input_structure 2, variation 3, judgment 3, checkability 4, stakes 3; A1 = Y, A2 = N, A3 = N, A4 = Y.
  - A11: R1–R7 = N, accountability = Y; D1–D3 = Y; stakes 5.

- [ ] **Step 1: Write the failing tests**

```python
def test_cgs_example_verdicts(acceptance_run):
    st = acceptance_run.steps
    assert st["D1"].call == st["D5"].call == st["D8"].call == "Remove"
    assert st["A2"].call == "Keep" and st["A2"].tier.standardize_first and st["A2"].tier.tier == "Rules"
    assert st["A6"].call == "Keep" and st["A6"].tier.tier == "Agent" and st["A6"].tier.approval
    assert st["A11"].call == "Keep" and st["A11"].tier.tier == "Human"
    assert all(st[s].call in ("Remove", "Combine") for s in ("A3", "A4"))

def test_cgs_aggregate_reported(acceptance_run, capsys):
    kept = [s for s in acceptance_run.steps.values() if s.call == "Keep" and s.tier]
    share = sum(s.tier.tier in ("Rules", "Human") for s in kept)
    print(f"{share} of {len(kept)} kept steps end at Rules or Human (CGS: 17 of 25 on their 28-step map)")

def test_acceptance_future_graph_valid(demo_model, acceptance_run):
    assert build_future_model(demo_model, acceptance_run)[0].validate_graph() == []
```

- [ ] **Step 2: Run** `pytest tests/test_acceptance.py -v -s`. Expected: FAIL until the fixture is complete, then PASS.
- [ ] **Step 3: Complete** the fixture with real quotes (search the two transcripts in `demo_data/` for each).
- [ ] **Step 4: Run** `pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `test: acceptance test on CGS's worked example`.

---

### Task 15: App screens and live check

**Files:**
- Modify: `app.py`
- Create: `scripts/phase3_live_check.py`
- Test: manual, in the browser (Streamlit UI), plus `pytest -v` stays green

**Interfaces:**
- Consumes: everything above.
- App structure: a top-level `st.segmented_control` (or `st.radio`, horizontal) "Stage 1 · Current state" / "Phase 3 · Future state", stored in `ss.view`.
  - **Stage 1**: tabs reordered to Swimlane, Process model, Pain points & open items, Data contract, Sources, Agent run (last). Save project and the "Mark map as reviewed" toggle on the Swimlane tab.
  - **Phase 3** tabs, each body wrapped in a helper `safe(fn)` that catches exceptions and shows `st.error` so one screen can't blank the others:
    1. *Run*: "Run framework" (calls `run_framework` with `agent_run=ss.run`, `map_reviewed=ss.map_reviewed`, streams the trace like Stage 1), "Re-run AI steps" (force_live), a warning when the map isn't marked reviewed, and the trace in a collapsed expander.
    2. *Gate & context*: the 8 checks with value, basis, reason and quote; score and band; success-end selector (end events; changing it reruns `apply_rules`); context answers; map checks.
    3. *Steps*: the step table (`step_table`) in `st.dataframe` with row selection; the selected step shows every answer grouped by family with basis badge, quote, reason and confidence, and an override control per answer that calls `answer_and_rescore` (show `ValueError` text with `st.error`). Below the answers: the step's future chain and the spec for each new step (`specs`, rendered as labelled fields), and any Stage 1 pain points on that step.
    4. *Follow-ups*: the follow-up list as a form (one input per question, allowed values as a selectbox where `allowed_values` is set), "Save answers" applies them one by one with `answer_and_rescore`; download `follow_ups_markdown`.
    5. *Future state*: diagram from `build_future_model` + `render_svg(..., markers=markers, title=f"{name} — future state")`, "Show what changed" toggle (`show_removed`), the summary (`summary_markdown`), downloads: draw.io, SVG, PNG, step table CSV and JSON, summary Markdown, follow-ups Markdown, full run JSON.
  - `ss.framework` is saved in the project file (Task 2).
- `scripts/phase3_live_check.py`: loads the demo project state (demo model + parsed demo transcripts), runs `run_framework(force_live=True)`, prints, for the Task 14 verdicts, which the live run reached and which differ (with the deciding answers), and the aggregate line. `--record` writes the run to `demo_data/framework_run_sales_process.json` (the replay). Exit code 0 always (agreement is reported, not enforced).

- [ ] **Step 1: Implement** the Stage 1 reorder and the Phase 3 screens.
- [ ] **Step 2: Run** `pytest -v`. Expected: all PASS.
- [ ] **Step 3: Check in the browser.** Restart Streamlit on port 8502. Load the demo → Analyze → mark reviewed → Phase 3 → Run framework. Without an API key and before `--record`, expect the "No API key" error on the Run screen while the other Phase 3 screens still render. With a key: run completes; answer one follow-up (for example volume) and confirm the step table and summary update with no new trace entries; toggle "Show what changed"; download the draw.io file and open it in draw.io.
- [ ] **Step 4: Live check** (needs `ANTHROPIC_API_KEY`): `.venv/bin/python scripts/phase3_live_check.py --record`. Expected: a printed agreement report and `demo_data/framework_run_sales_process.json` written. Then reload the app without the key and confirm Phase 3 replays.
- [ ] **Step 5: Commit** `feat: Phase 3 screens, Stage 1 reorder, live check and replay`.
