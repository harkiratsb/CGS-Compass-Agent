# CGS Compass — AI-enabled process discovery and redesign

Two stages in one Streamlit app:

- **Stage 1 · Current state** — ingest process materials (transcripts, docs) → a multi-agent extraction builds a
  structured current-state model → a CGS-standard swimlane → human review (in the app or in draw.io).
- **Phase 3 · Future state** — runs CGS's *Process Improvement Framework* (Parts 0–5) on the reviewed map and recommends
  a future-state process: which steps to remove, combine or move, and whether each remaining step should be
  **Rules**, an **Agent**, a **Human approval** or a **Human task**. Outputs a future-state diagram, a step table,
  a summary with expected impact, and the top 10 follow-up questions.

## Run it

Needs Python 3.11+. No API key needed for the demo.

```bash
git clone https://github.com/harkiratsb/CGS-Compass-Agent.git
cd CGS-Compass-Agent
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then, in the browser (http://localhost:8501):

1. Click **Load demo materials**, then **▶ Analyze process**.
2. Switch to **Phase 3 · Future state** at the top and click **Run framework**.

**Optional:** to run on your own files, copy `.env.example` to `.env` and add your `ANTHROPIC_API_KEY`.
Without a key, only the demo works (it replays recorded runs).

**Tests:** `pip install -r requirements-dev.txt && pytest -q`

<details>
<summary>More setup details</summary>

- **Optional `.env` settings:** `CGS_MODEL` (default `claude-sonnet-5-5`), `CGS_REVIEW_MODEL` (default
  `claude-opus-5-5`), and `GOOGLE_SERVICE_ACCOUNT_FILE` / `DRIVE_FOLDER_ID` for Google Drive import (see `ingest/drive.py`).
- **PNG export** needs the Cairo library (`brew install cairo` / `apt install libcairo2`). SVG and draw.io exports work without it.
- **Live runs** are cached in `.cache/`. A live Phase 3 run takes about 10 minutes.
  `python scripts/phase3_live_check.py` compares a live run with CGS's worked example; `--record` refreshes the demo replay.
- **Save project** / **Open project** keep your edits and answers between sessions.

</details>

## Phase 3 · How the framework runs

The AI only **answers** the framework's judgment questions; plain code **decides**.

- Each answer (R1–R7 "should it exist", D1–D3 standardization, six dials, the agent test, readiness) carries a quote, a
  reason, an H/M/L confidence and a basis: **stated** (quoted from the materials), **assumed** (CGS's cautious default
  when the materials don't say) or **answered** (a person, in the app). Missing evidence can only lower a score.
- `framework/rules.py` applies every CGS rule — doc gate, calls, tiers, readiness, posture, waves — from
  `framework/settings.toml`. CGS's thresholds are a first pass: change a number there and rerun, no prompt changes.
- Every Stage 1 output feeds the AI as evidence: pain points, open questions, source disagreements, handoffs,
  dependencies, tools, step sizing.
- A second model verifies stated answers against their quotes; it can only downgrade.
- Answers you give survive re-runs; the top 10 follow-ups are the ones whose answers could change a result (plus
  volume, which the impact section needs).

Method: `docs/cgs-framework-starter.md` · Design: `docs/superpowers/specs/2026-11-18-phase3-framework-design.md`.

## How it maps to the CGS framework

| CGS agent (their doc) | This app | Where |
|---|---|---|
| Interview & Transcript Synthesizer | extraction prompt + schema validation | `ingest/extract.py` |
| "Second Brain" Knowledge Ingestion | file parsing with source refs | `ingest/parse.py` |
| SOP & Standard Process Drafter | deterministic JSON → swimlane | `render/swimlane.py` |
| Process vs Workflow Disambiguation (Phase 3 hook) | `step_type` on every activity | `schema.py` |

The pipeline itself is a **hybrid flow**: deterministic gates (parse → validate → render) around one agentic module (extraction).

## The agent (ingest/agent.py)

```
            ┌ Extract source A ┐
Plan ───────┼ Extract source B ┼─▶ Compare sources ─▶ Reconcile ─┐
            └ Verifier: blind checklist (sources only) ──────────┤
                                                                  ▼
              Verifier checks map vs checklist ─▶ findings ─▶ Reviser applies / rejects   (≤2 rounds)
                                                                  ▼
                                                              Validate
```

| Role | Model | Sees |
|---|---|---|
| Extractor (per source), Comparer, Reconciler, Reviser | `CGS_MODEL` (default claude-sonnet-5-5) | sources, models |
| Verifier | `CGS_REVIEW_MODEL` (default claude-opus-5-5) | sources; builds its checklist **before** seeing any map; never edits the map |

- **Compare sources** produces a coverage matrix (which steps each source has) and a discrepancy list
  (missing / order / owner / logic / naming / detail) with what each source says. The reconciler must resolve
  every discrepancy by id; unresolved ones are flagged.
- **Every step's output JSON** is viewable and downloadable in the **Agent run** tab, plus the full run.
- Failures degrade gracefully (truncation → shorter retry; failed step → ⚠️ and continue).
- Live runs are cached in `.cache/agent_<hash>.json`; without a cache, the demo transcripts load
  `demo_data/agent_run_sales_process.json`, labelled **replay** in the UI.

## Swimlane conventions

`render/swimlane.py` follows the CGS Process Documentation Standards: horizontal lanes by role (no personal names),
BPMN 2.0 shapes (start/end events, tasks, decision diamonds labelled as questions with Yes/No branches, ⊞ predefined
subprocess), flow upper-left → bottom-right, right-angle connectors, black & white core diagram. Colour is reserved for
callouts: yellow sticky notes for open decisions or missing rules, italic text callouts under a task for reviewer
context (`Activity.notes`). Task labels are Verb + Noun. The **Analysis overlay** toggle adds step-type tint,
pain-point badges and source counts on top.

**Connector routing.** Every connector is routed on an 8px grid (A* search). Shapes and sticky notes are obstacles;
text callouts, tool tags and lane lines are avoided. Two connectors never share a line unless they merge into the same
step, each branch of a decision leaves from its own side of the diamond, and loops back to an earlier step are dashed.
Lettered in-page connectors are used only for backward jumps across lanes or rows.

**CGS tool tags.** Tasks show the tools they use above the shape, tagged with the CGS tool list (`cgs_tools.py`):
`TL-01 · Calendar`, `TL-08 · Insightly`. Bundled entries keep the specific app as the label. A tool that is not on the
list shows its plain name in a dashed tag ("not on CGS tool list"). Documents and templates (sales plays, intro docs)
are kept in the step note, not tagged. The extraction prompt carries the list and these rules, and only tags tools
the sources name. Tool names stay editable in the Process model tab; the **CGS tags** column shows how they resolve.

**Editable export.** *Download draw.io* (`render/drawio.py`) writes the same diagram as a draw.io file: lanes are
swimlane containers, steps are real shapes inside their lane, and connectors are attached to the shapes, so moving a
step (or dragging it into another lane) keeps its connectors. Tool tags and callouts move with their step. Open it in
draw.io, or in Lucidchart via **File → Import → draw.io**.

**Edit in draw.io and bring the edits back.** *Open in draw.io* opens the diagram straight in draw.io in the browser
(no account; the diagram travels compressed inside the link, nothing is uploaded by the app). After editing, save it
as a `.drawio` file and upload it under *Upload edited draw.io file*. `ingest/drawio_import.py` compares it with the
current model and lists every change before anything is applied: steps moved to another lane or along the flow,
renamed steps / decisions / lanes, reordered lanes, steps and lanes added or deleted, connectors added, removed,
re-pointed or relabelled, tool tags removed or edited, notes edited. **Apply changes** updates the process model
(and so the handoff JSON); the app then redraws the layout and connectors itself. Sticky notes, colours and connector
bends drawn by hand are not read back. Both plain and compressed draw.io files are supported.

## Data contract (Stage 1 → Phase 3)

`schema.py` is the contract. Phase 3 consumes `ProcessModel` as-is:

```
process, roles[], activities[] (role, order, kind, tools, assumptions, step_type, source_refs),
decisions[] (branches), edges[], handoffs[], dependencies[], pain_points[], open_questions[]
```

`ProcessModel.validate_graph()` guarantees every branch/edge points at a real node before anything renders.

## Pain-point handoff (Stage 1 → opportunity mapping)

`handoff.py` turns the model into the JSON CGS's opportunity-mapping step reads: format
`cgs.compass.painpoints/v0.2` (CGS's proposed v0.1, with additive changes). Download it from **Data contract →
Pain-point handoff**; the app checks allowed values, references, quote length and names before download. Intake
fields for it (account, process ID, function ref, definition status, extra names to remove) are under **Handoff
details** in the sidebar. Example output: `demo_data/handoff_sales_process.json`.

- **Per-step sizing** (`activities[]`): `purpose` (intent / execution / audit), `control_now` (C0–C5) and
  `human_minutes_per_cycle` (+ optional range) on every task and decision, each marked `stated` or `estimated`.
  The agent fills them (extraction prompt); reviewers edit them in **Process model → Step sizing**.
- **Pain points**: `type` is mapped from the agent's category; `frequency` is set by the agent and editable in the
  Pain points tab; `data_touched` comes from the step's CGS tool tags; `human_judgment` from the step;
  `confidence` is rule-based on evidence; `grounded` is null (no automated grounding check).
- **Roles, not names**: the prompt asks for roles in every field including quotes; on export, speaker names (and
  common nicknames) plus any extra names you list are replaced with `[team member]`.

In the demo replay, sizing values and frequencies were filled in by hand from the transcripts and are all marked
`estimated` (except Gemini note-taking on *Conduct lead meeting*, which the transcript states).

## Layout

```
app.py                     Streamlit UI: Stage 1 tabs + view switch to Phase 3
phase3_ui.py               Phase 3 screens (run, gate & context, steps, follow-ups, future state)
project.py                 save / open a project file (materials, model, runs, answers)
intake.py                  default intake values
schema.py                  pydantic data contract (Stage 1 → Phase 3)
ingest/                    parsing, extraction agent, merge, Drive import, draw.io re-import
render/swimlane.py         ProcessModel → SVG swimlane (+ PNG via cairosvg), step-type markers
render/drawio.py           same diagram as an editable draw.io file (Lucidchart import)
framework/settings.toml    every CGS threshold and band
framework/rules.py         all CGS rules (Parts 0–5), answering follow-ups, rescoring
framework/agent.py         Phase 3 AI steps, prompts, cache and replay
framework/followups.py     ranks the top 10 follow-up questions
framework/future.py        step table → future-state process model
framework/impact.py        expected impact (minutes saved, hours a month, roles freed)
framework/outputs.py       step table CSV/JSON, summary and follow-up Markdown
cgs_tools.py               CGS tool list + tool-name → TL-xx tag resolution
handoff.py                 pain-point handoff JSON (cgs.compass.painpoints/v0.2) + name scrubbing
scripts/phase3_live_check.py  live Phase 3 run on the demo, agreement report, --record
demo_data/                 two sales-process transcripts + recorded Stage 1 and Phase 3 runs
tests/                     pytest suite
```

## Not in this build (next steps)

- Tune the Phase 3 prompts: the recorded live run reaches 6 of CGS's 8 worked-example verdicts.
- Phase 3 boxes the framework doesn't cover yet: Minimum Viable Human Organization, Orchestrator, naming APIs and
  sub-agents per agent step.
- Phase 1 gaps: audio/video transcription (bring transcripts as text), Stakeholder Consensus agent, Obsidian / past
  deliverables ingestion, Google Drive picker (OAuth).
- Multi-process runs.
