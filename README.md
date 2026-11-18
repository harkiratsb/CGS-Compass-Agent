# CGS Process Discovery — Stage 1: Ingestion & Current-State Mapping

Proof-of-concept for the first stage of the CGS AI-enabled process analysis MVP:
ingest process materials → extract a structured current-state model → render a swimlane → human review.

## Run it

```bash
cd cgs-stage1
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Opens at http://localhost:8501. Click **Load demo materials** → **Analyze process**.

### Live vs cached extraction

| | |
|---|---|
| `ANTHROPIC_API_KEY` set in `.env` | Live: Claude extracts the model from whatever you upload. Results are cached in `.cache/` by file hash. |
| No key | Cached: the two demo transcripts resolve to `demo_data/extraction_sales_process.json`. Any other upload will show a clear error. |

Copy `.env.example` → `.env` to add a key. `Force live re-extraction` in the sidebar bypasses the cache.

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

## Data contract (Stage 1 → Stage 2)

`schema.py` is the contract. Stage 2 (analysis) consumes `ProcessModel` as-is:

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
app.py                     Streamlit UI (intake → analyze → review tabs)
schema.py                  pydantic data contract
ingest/parse.py            md/txt/vtt/srt/docx/pdf → chunks with location tags
ingest/extract.py          Claude call, JSON validation, retry, cache
ingest/merge.py            cross-source dedupe + source-coverage counts
render/swimlane.py         ProcessModel → layout + connector routing → SVG swimlane (+ PNG via cairosvg)
render/drawio.py           same diagram as an editable draw.io file (Lucidchart import) + Open-in-draw.io link
ingest/drawio_import.py    edited draw.io file → change list → updated ProcessModel
cgs_tools.py               CGS tool list + tool-name → TL-xx tag resolution
handoff.py                 ProcessModel → pain-point handoff JSON (cgs.compass.painpoints/v0.2) + checks
demo_data/                 two CGS sales-process transcripts + cached extraction
```

## Not in this build (next steps)

Google Drive picker (OAuth), audio transcription (bring transcripts as text), Stakeholder Consensus agent, multi-process, Stage 2 analysis.
