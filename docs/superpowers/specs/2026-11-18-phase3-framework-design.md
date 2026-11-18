# Phase 3: CGS Process Improvement Framework — design

Date: 2026-11-18 · Status: draft for approval

## 1. Intent and why

CGS's roadmap has three phases. Phase 1 (discovery) and Phase 2 (process mapping) are what this repo does today:
transcripts in, a reviewed current-state swimlane out. The swimlane is the Phase 2 SOP deliverable.

Phase 3 (Workflow Design & Agentic Architecture) starts from that reviewed map and recommends a future-state process.
CGS has written the method for it: *Process Improvement Framework: Starter for Team Compass* (2026-11-11), Parts 0 to 5.
For every step it answers three separate questions: should the step exist, how far should it be automated (Standardize,
Rules, Agent, Human), and is the business ready to change it now. It outputs a future-state diagram for Lucid, a step
table and a summary.

The person running the app is Harkirat, on behalf of CGS. CGS receives the outputs. CGS calls its thresholds a first pass
and wants feedback from real runs, so the first goal is **a complete, rough run of the whole framework** on a real
process, not a polished half.

**The method itself** (every check, R1–R7, D1–D3, dial anchors, agent test, readiness tests, posture, waves, spec
templates and CGS's worked example) is transcribed in [`docs/cgs-framework-starter.md`](../../cgs-framework-starter.md).
Prompts and rules are written from that file. This design doesn't repeat it.

## 2. Goals, non-goals, anti-goals

**Goals**
- Run Parts 0 to 5 end to end on any reviewed Stage 1 process model, and produce the three CGS deliverables: the
  future-state diagram (draw.io, importable into Lucid, plus SVG), the step table and the summary.
- Work on any input. Nothing may depend on what a particular transcript happens to contain.
- Every framework input carries its evidence: a quote when stated, the framework default when not, a person's answer
  when given. Every call carries a one-line reason and an H/M/L confidence.
- Missing inputs never stop a run. They become follow-up questions. Answering one rescores instantly, with no AI call.
- CGS's rules and thresholds live in one settings file, so a threshold change is a one-line edit plus a test run.
- CGS's own example verdicts on the sales process are an automated acceptance test.

**Non-goals (this design)**
- Minimum Viable Human Organization and the Orchestrator (Phase 3 boxes the framework doesn't cover yet).
- Naming concrete APIs and sub-agents for each agent step (beyond the framework's "systems it reads and writes").
- Phase 1 gaps: audio/video input, the Stakeholder Consensus agent, Obsidian / past-deliverable ingestion.
- Tuning the scores to be "right". Rough is the brief; the run exists to get CGS feedback.
- The wider Stage 1 screen clean-up from the 2026-11-18 review (merging duplicated panels etc.). Only the structural
  changes Phase 3 needs are in scope here (section 8). The rest is a separate follow-up.
- Changing the pain-point handoff file (`handoff.py`, `cgs.compass.painpoints/v0.2`). It stays as is until CGS says
  whether opportunity mapping still consumes it.

**Anti-goals (failure even if it "works")**
- The AI applying CGS's rules. The AI answers questions; code makes the calls. If a call can't be traced to answers +
  a rule in the settings file, it's wrong.
- Missing evidence raising a score, or a guessed fact shown as stated.
- A run that blocks or errors because the materials lack goals, volume, readiness facts, etc.
- A future-state diagram that disagrees with the step table. The diagram is drawn from the table, never edited apart
  from it.

## 3. Constraints

- Existing stack: Python 3.14, Streamlit, pydantic v2, Anthropic SDK. No new heavy dependencies. `tomllib` is in the
  standard library, so the settings file is TOML.
- `schema.py` (`ProcessModel`) is the Stage 1 → Phase 3 contract. Phase 3 reads it and does not change its fields.
- Models: same env vars as Stage 1 (`CGS_MODEL` for the main agent, `CGS_REVIEW_MODEL` for the verifier).
- Replay must keep working without an API key for the demo materials, as Stage 1 does today.
- Names: roles only, never personal names, in every output (same rule and safety net as Stage 1).

## 4. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| 1 | One agent does everything: answers, scores, calls, tiers in one pass | Fastest to build. Rules applied by the AI vary run to run, can't be unit-tested, and every threshold change is a prompt rewrite. |
| **2** | **AI answers the judgment questions; code applies CGS's rules (chosen)** | More structure up front. Calls are deterministic and testable, thresholds tunable in one file, follow-up answers rescore with no AI call, and it matches Stage 1's hybrid pattern (code gates around agentic steps). |
| 3 | One AI agent per Phase 3 box plus an orchestrator | Matches the roadmap names. More calls and cost, and the rules still have to live in code. |

Harkirat chose **2**. From 3 we keep the naming: each pipeline step is labelled in the run trace with the Phase 3 agent it
stands for, so CGS sees its roadmap in the run.

## 5. Prerequisites (do first)

1. **Save and reopen a project.** Today a reviewed model lives only in the Streamlit session; a refresh loses it.
   Add a project file (JSON) holding: the Stage 1 `ProcessModel`, the Stage 1 `AgentRun`, the parsed source chunks
   (needed for Phase 3 evidence), and the Phase 3 `FrameworkRun` plus answers when present. "Save project" downloads it;
   "Open project" uploads it and restores everything. Without this, Phase 3 can't start from a reviewed map.
2. **Mark the map reviewed.** A "Mark map as reviewed" action on Stage 1. Phase 3 can run on an unreviewed map, but the
   summary states that it did.
3. **Tests.** Add `pytest` and a `tests/` folder. Rules, settings loading and the acceptance test all run without an API
   key (recorded AI answers as fixtures).

## 6. Data

New package `framework/` with its own pydantic models, referencing Stage 1 node ids. `ProcessModel` is unchanged.

**The answer type** (used for every framework input):

```
Answer
  value        # Y / N / Unknown, Y / Partly / N, 1-5, text, or number — typed per question
  basis        # "stated" (from the materials) | "assumed" (framework default) | "answered" (a person, overrides)
  reason       # one line
  confidence   # H | M | L
  quotes[]     # SourceRef (file, location, quote) — required when basis == "stated"
  question     # the follow-up question to ask when basis == "assumed"
```

Rules for `Answer`:
- `stated` without a quote is invalid; the validator downgrades it to `assumed`.
- `assumed` takes the framework default (section 7) and always produces a follow-up question.
- `answered` is set only by a person in the app; it keeps the previous value in history so it can be undone.

**Process level (`FrameworkRun`)**
- `doc_gate`: the 8 Part 0 checks, each an `Answer` (Y / Partly / N), the score (Y = 1, Partly = 0.5), and the
  confidence band (High 7–8, Medium 4–6.5, Low < 4).
- `context`: outcome, goal, measures + baselines, volume (runs per period), context-read paragraph — each an `Answer`.
- `map_checks[]`: owner questions from Part 1 (type: alternatives-as-sequence, note-is-hidden-step, duplicate gate,
  SOP/map mismatch, dead end), each tied to step ids. Not scored.
- `readiness_defaults`: the five Part 4 tests at process level (Governance and People are usually process-wide).
- `redesign_potential` (1–5), `posture`, `waves`, `follow_ups[]` (all derived by code), `summary` (text).
- `trace[]`: the run steps, same shape as Stage 1's `AgentStep`, labelled with Phase 3 agent names.

**Per step (`StepAssessment`)**, one per task, decision and subprocess in the reviewed map:
- `worksheet`: who, type today (Rules / Agent / Human approval / Human task), channel or variant, system, volume,
  minutes (prefilled from Stage 1 `tools`, `human_minutes_per_cycle`, `step_type`), notes.
- `part2`: R1–R7 as `Answer` (Y / N / Unknown), pass 1 and pass 2 (context) values, the resulting call
  (Keep / Remove / Combine / Move / Reimagine / Fix the map), the step it combines with (if any), protected flag
  (decision or accountability step).
- `tier1`: D1 Digital, D2 Standard, D3 Findable (Y / Partly / N).
- `dials`: Rule clarity, Input structure, Variation, Judgment, Checkability, Stakes (1–5).
- `agent_test`: A1–A4 (Y / N).
- `tier_result`: Human / Rules / Agent / Agent + Human approval / Rules + Human task / Mixed (split or rescore)
  / Standardize first, plus which rule fired.
- `readiness`: the five tests (inheriting process defaults unless overridden per step) and the minimum.
- `recommendation`: the Part 5 per-step action, its wave, and the future-state chain (ordered list of new steps, each
  typed Rules / Agent / Human approval / Human task).
- `new_step_specs[]`: the Part 5 spec for each new step (Rules: owner, logic, test cases, run record; Agent: goal,
  systems read/written, never-dos, handoff to a person, logs, error budget, failure modes, down behaviour;
  Human approval: authority, what they see, what they decide, speed, record; Human task: what they do, skills, why it
  stays human).

## 7. Rules and the settings file

All of CGS's arithmetic and decision logic lives in `framework/rules.py`, driven by `framework/settings.toml`. Code,
not the AI, does every item below. Values as in the CGS starter:

- **Part 0**: score = sum(Y = 1, Partly = 0.5); High ≥ 7, Medium ≥ 4, Low < 4. **Medium (decided, confirm with CGS):**
  the AI's values are kept, every later answer gets an "assumed — doc confidence Medium" flag, and confidence is capped
  at M. Values are *not* replaced by framework defaults (that would turn almost every input into a default and make
  the run meaningless; the demo map is likely to score Medium on checks 6–8). Low stops scoring and the posture is "Document first" — the run still
  produces the doc gate, context, map checks and follow-ups. A missing SOP on a Rules or Agent step adds a
  "Write the SOP" action. **SOP rule (decided):** the swimlane plus each step's description and notes count as the SOP.
  A step with how-to detail scores Y on check 8; a step with only a name scores Partly. (Confirm with CGS.)
- **Part 2**: Unknown counts as No and is marked assumed. R1 → Remove; R2 → Remove; R3 → capture once (Combine);
  R4 → Remove or Move (the AI's R4 answer also says which: Move when the step is needed but sits where it causes the
  wait, Remove otherwise); R5 → Remove and fix upstream; R6 → Combine (adjacent, same actor — adjacency and actor are
  checked by code from edges and roles, the AI only judges "could merge"); R7 → Fix the map. Decision and
  accountability steps are kept, **except** when R2 says the step exists only for an access limit, handoff, info
  passing, habit or untrusted systems. (Decided from CGS's own example, which removes the CRM-access check even though
  it is drawn as a decision. Protection covers decisions about the work, such as "Lead responds?" or "Aligned on the
  offering?", not gate checks.) First matching rule wins in R1…R7 order. Redesign potential from the share
  of non-Keep calls: < 10% → 1, 10–24 → 2, 25–39 → 3, 40–59 → 4, ≥ 60% → 5.
- **Tier 1**: anything short of D1 = D2 = D3 = Y → "Standardize first".
- **Dial defaults**: no evidence → 3 "assumed", except Rule clarity → 1.
- **Agent test**: pass = (A1 or A2) and not A3 and A4.
- **Tier decision, first match wins**: (1) Stakes 5 → Human (agent may prepare only). (2) Clarity ≥ 4, Input ≥ 4,
  Variation ≤ 2, Judgment ≤ 2 → Rules. (3) Checkability ≥ 3 and (Judgment ≥ 3 or Input ≤ 2 or Variation ≥ 3) → Agent
  candidate: Agent if the agent test passes (+ Human approval if Stakes ≥ 3, + error budget), else Rules + Human task.
  (4) Judgment ≥ 3 and Checkability ≤ 2 → Human. (5) Otherwise Mixed: split or write the rule down and rescore.
  (6) An agent with no approver only with guardrails, limits and an audit trail (spec fields required).
- **Loop (decided):** one extra **AI** pass, after step 6. The Step Removal agent is shown the Tier 1 and Tier 3
  results and asked two things only: which R1–R7 answers change now that steps are standardized, and which groups of
  steps an Agent step makes unnecessary (a judgment, so it is the AI's). Code then recomputes calls and tiers once and
  stops. Anything still changing is listed as a follow-up. This pass has its own trace entry, cache and replay entry.
  Answering a follow-up never triggers it; only "Re-run AI steps" does.
- **Part 4**: readiness = min of five tests; no evidence → 1 "unverified"; Ready = 4–5; below 4 = "harden first"
  (sequencing, not a stop).
- **Part 5 per step**: flagged in Part 2 → change the process (Fix the map → owner question, rescore later); not
  digital + standard → Standardize first; else the tier result; readiness 4–5 → automate / agentify (approver if
  Stakes ≥ 3), else harden first.
- **Posture, first true line wins**: doc confidence Low → Document first; open map questions on high-flow steps → Fix
  the map; redesign 4–5 → Redesign first; redesign 3 → redesign flagged segments, then standardize and automate the
  rest; redesign 1–2 → Incremental; add "harden first" when not ready. "High-flow step" = on the main path: every step on
  any path from start to the **success end**. Code proposes the success end (the end event reached by the longest
  path); you can change it on the Gate & context screen.
- **Waves**: 0 Fix and remove; 1 Standardize (fails D1–D3); 2 Automate (Rules); 3 Agentify (passed agent test).
  A step's wave is the earliest it still needs; later waves are listed but blocked until earlier ones clear.

`settings.toml` holds every number and band above (gate bands, redesign bands, dial defaults, tier thresholds, ready
threshold, loop limit). Rule *order* stays in code. A test fails if a threshold in code is not read from settings.

## 8. Pipeline

Same shape as Stage 1's `ingest/agent.py`: code steps (⚙️) around AI steps (🤖), each recorded in the trace with its
output JSON, cached by input hash, with a replay file for the demo.

| # | Step (Phase 3 label) | Kind | Does |
|---|---|---|---|
| 1 | Load | ⚙️ | Reviewed `ProcessModel` + source chunks from the project. Derive graph facts: adjacency, same-actor neighbours, main path, protected steps. |
| 2 | Doc gate | ⚙️ + 🤖 | Code scores checks 1, 2, 4, 5, 6, 7 from the model. AI judges 3 (Verb + Noun) and 8 (SOP detail). |
| 3 | Context reader (Toil Task Assessor) | 🤖 | Outcome, goal, measures, baselines, volume, context paragraph, map checks, worksheet fields per step. |
| 4 | Step Removal, pass 1 | 🤖 then ⚙️ | AI answers R1–R7 per step from the map as written. Code makes the calls. |
| 5 | Step Removal, pass 2 (context) | 🤖 then ⚙️ | AI revisits only steps where business context changes an answer. Code recomputes calls and redesign potential. |
| 6 | Disambiguation | 🤖 then ⚙️ | AI answers D1–D3, six dials, A1–A4 for every step not removed. Code applies tier rules; runs the one-pass loop. |
| 6b | Step Removal, loop pass | 🤖 then ⚙️ | The one extra pass in section 7: changed R answers after Tier 1, groups an agent makes unnecessary. Code recomputes once. |
| 7 | Readiness | 🤖 then ⚙️ | AI gathers evidence for the five tests (process level, per-step overrides). Code takes the minimum. |
| 8 | Verifier | 🤖 (review model) | Checks every `stated` answer against its quote. It can only downgrade: to `assumed`, or lower confidence. It never raises a score or changes a value to a "better" one. |
| 9 | Recommend | ⚙️ | Part 5 per step, posture, waves, follow-up list. |
| 10 | Workflow Documentation & Specification | 🤖 | Future-state chain per kept step and the spec for each new step. |
| 11 | Draw | ⚙️ | Builds a future-state `ProcessModel` from the step table and renders it (section 9). |
| 12 | Summary | ⚙️ + 🤖 | Code fills every number; AI writes the context paragraph and "first three actions" from the computed results only. |

Batching: steps 4–7 and 10 each send the whole process in one call (adjacency and context matter), splitting by lane
only if the output would not fit. Exact prompt wording is left to the builder; prompts must forbid making calls or
applying thresholds and must require a quote for every `stated` answer.

**Answering follow-ups.** An answered value sets `basis = answered` and reruns steps 9, 11 and 12 (plus the code half of
4–7). No AI call. A "Re-run AI steps" button exists for when answers change the picture a lot.

## 9. Future-state diagram

- Built from the step table: removed steps dropped and their incoming edges re-pointed to the next kept step; combined
  steps merged into the survivor; each kept step replaced by its chain (e.g. Agent → Human approval → Rules) in the
  same lane unless the chain says otherwise. Agent and Rules steps go in a new "Automation" lane (decided: one lane,
  keeps the human lanes readable); Human approval and Human task stay in the role's lane.
- Rendered with the existing `render/swimlane.py` and `render/drawio.py`. New shape per step type, black and white
  (CGS standard): Rules = task with a gear marker, Agent = task with a double border, Human approval = task with a
  check marker, Human task = plain task. Legend updated. Removed steps can be shown greyed on a toggle ("show what
  changed").
- Exports: draw.io (Lucid via File → Import → draw.io), SVG, PNG. Title "— future state".
- Exact marker artwork is left to the builder within the black-and-white rule.

## 10. Outputs

- **Step table**: one row per current step: step, lane, call, tier result, future chain, wave, readiness, one-line
  reason, confidence, gaps (count of assumed inputs). Shown in the app; exported as CSV and as JSON.
- **Summary**: outcome and goal, doc confidence, open map questions, redesign potential, steps by tier, readiness by
  test, posture, waves, first three actions, gaps. Exported as Markdown. Missing evidence is shown as a gap, never guessed.
- **Follow-up questions**: the list of every `assumed` input, grouped (process facts first, then by step), each with
  where the answer goes. Exported as Markdown so it can be sent to CGS.
- **Full run JSON** (`FrameworkRun`) for debugging.

## 11. Screens

The app gets two journeys (Streamlit multipage or a top-level switch — builder's choice):

- **Stage 1 · Current state** (existing tabs, reordered so Swimlane is first and the agent trace is last; "Save project"
  and "Mark map as reviewed" added).
- **Phase 3 · Future state**:
  1. *Run* — start / replay, trace (collapsed, Phase 3 labels).
  2. *Gate & context* — doc gate checks with score and band, context answers, map checks.
  3. *Steps* — the step table; selecting a step shows all its answers with basis, quote, reason, confidence, and lets
     you override any answer (sets `answered`).
  4. *Follow-ups* — the assumed-input list as a form; answering rescores.
  5. *Future state* — diagram with "show what changed" toggle, summary, exports.

One error inside a Phase 3 screen must not blank the other screens (wrap each screen).

## 12. Tests and acceptance

- **Unit tests** for every rule in section 7, table-driven from `settings.toml`, including first-match ordering, the
  Unknown-counts-as-No rule, defaults, the "never raise" verifier rule, and Answer validation (stated without quote →
  assumed).
- **Graph tests**: removing and combining steps keeps the future-state graph valid (`validate_graph()` empty).
- **Two layers (decided).** (1) *Rules acceptance, blocking, no API key:* a hand-curated answer set for the demo
  process (`tests/fixtures/`, separate from the demo replay file) must give CGS's verdicts below. This proves the rules
  code. (2) *Prompt agreement, reported, not blocking:* the live-check script runs the AI on the demo and reports how
  many of the verdicts it reaches and where it differs. A miss there is a prompt-tuning item, not a failed build. The
  demo replay file is recorded from a real live run, as Stage 1's is.
- CGS's example verdicts:
  - D1 "Has Insightly access?" before entering a lead → **Remove** (D5 and D8, the same check later, should match).
  - A2 "Add lead to Insightly" → **Keep**, Standardize first, then **Rules**. (A3/A4, the request-and-enter path that
    only exists because of the access check, should come out Remove or Combine.)
  - A6 "Contact lead" → **Keep**, **Agent + Human approval**.
  - A11 "Conduct lead meeting" → **Keep**, **Human**.
  CGS's aggregate ("17 of the 25 remaining steps end at Rules or Human" on their 28-step map) is reported, not
  asserted, because our map has a different step count.
- **Live check**: a script that runs the AI steps on the demo materials and prints the diff against the replay, for
  regression when prompts change.

## 13. Decided here vs left to the builder

**Decided by Harkirat**: build the whole framework end to end, rough (A); approach 2; runs on any input; swimlane is the SOP.

**My calls (please check)**: R2 overrides the "keep decision steps" protection for access/handoff gate checks;
a Medium doc gate flags answers and caps confidence rather than replacing values with defaults; the acceptance test
checks the rules on curated answers, and prompt agreement is reported separately; one extra AI loop pass in v1;
the success end is proposed by code and changeable; a single "Automation" lane for Agent and Rules steps; readiness
at process level with per-step overrides; the verifier can only downgrade; "high-flow" = on the main path; follow-up
answers rescore without AI; prerequisites (save/open project, mark reviewed, tests) come first; wider Stage 1 clean-up
is out of scope.

**Left to the builder**: prompt wording; Streamlit multipage vs switch; marker artwork; file and function names inside
`framework/`; batching thresholds; CSV column order.

## 14. Open with CGS (doesn't block the build)

- Do they read the swimlane + step notes as the SOP for Part 0 check 8?
- Does "Medium: every score assumed" mean a flag (our reading) or replacing values with defaults?
- The original starter document, to replace our transcription in `docs/cgs-framework-starter.md`.
- Is the pain-point handoff to opportunity mapping still needed alongside this framework?
- Feedback on thresholds after the first real run (the reason for building it rough).
