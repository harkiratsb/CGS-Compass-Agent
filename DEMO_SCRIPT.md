# Stage 1 demo script (≈8 min)

**Before the call:**
1. Tonight: run the agent live once (*Run agent live* ticked). The result is cached in `.cache/`.
2. Tomorrow: **untick** *Run agent live*. Analyze then loads last night's real run instantly (mode shows **live**).
   No API risk during the call, and everything shown is genuine agent output.
3. `streamlit run app.py` already open, demo materials loaded but not yet analyzed. Screen recording saved as a fallback.
4. **Re-read the Agent run tab before the call.** The reconciliation conflicts and self-review findings are whatever
   the live agent produced. Walk through the ones it actually found, not the examples below.

## 0:00 — The problem (30s)
"Today, mapping a process means Liz running a 40-minute Lucid session, then cleaning it up afterwards. The information lives in transcripts, docs and people's heads. Stage 1 takes those raw materials and produces a reviewable current-state model."

## 0:30 — Intake (30s)
Sidebar: process name, owner, scope. Two files selected: the workshop transcript (Liz/Stephan/Jordyn, ~40 min) and Jordyn's 12-minute walkthrough.
"Two different sources describing the same process — one messy, one clean. That's deliberate."

## 1:00 — Run the agent (30s)
Click **Analyze process**. The status panel streams each step: plan → two extractions and the verifier's blind
checklist in parallel → compare → reconcile → verify → revise → verify → validate.

## 1:30 — Agent run tab: the showcase (3 min)
Point at the trace: each step shows which agent and which model ran it. Open one **View output JSON** panel.
"Every intermediate artifact is inspectable. Nothing happens in a black box."

**1 · Independent extraction.** "Each source is mapped on its own first." Read the counts off the screen.

**2 · Discrepancies.** The coverage matrix highlights steps only one source mentions. Below it, each discrepancy
shows what each source says side by side, and how it was resolved. Pick the highest-severity one.
"Before merging, the agent makes the disagreements explicit, and the reconciler has to resolve every one by ID."

**3 · Reconciliation.** Skim it: merged / conflict / single-source / renamed / excluded.

**4 · Independent verification.** This is the key point on trust:
"The verifier runs on a different model. It read the transcripts and built its own checklist *before* seeing
the map, so it isn't anchored on the extractor's version. Then it checked the map against that checklist. It
doesn't edit anything. A separate reviser applies each finding or rejects it with a reason."
Walk through one applied finding and the rejected one. Rejected findings show the agents check each other, not
rubber-stamp each other.

**Ground truth (if asked about accuracy).** "We compared the output to Liz's live map: same eight decisions,
23 of 24 tasks, same loops. The one difference was a modelling choice: she put the agreed best-practice
scheduling into the flow, and we logged it as a conflict. That's now a convention we've learned."

## 4:00 — Process model tab (1 min)
- **sources** column: which steps both transcripts support, and which come from one.
- **step_type**: rule-based vs judgment, the hook for your Disambiguation and Toil agents.
- **Evidence** expander: every step traces back to file + timestamp + quote.

## 5:00 — Swimlane tab (1.5 min)
- Lanes = roles. Walk the happy path left to right: identify → Insightly → outreach → meeting → opportunity → discussion doc → alignment → proposal.
- Point out: the three Insightly-access decisions, the yellow open-item notes, the outreach loop, the "adjust focus" rework arc, the review lane.
- Flip **Analysis overlay** on: "this is what Stage 2 builds on."
- Download SVG. "This is the visual documentation you said matters most."

## 6:30 — Human review (45s)
Back to Process model → rename a step or change its lane → **Apply edits & regenerate** → swimlane updates.
"The model is the source of truth; the diagram is a view. Stage 2 consumes the same JSON."

## 7:15 — Pain points tab + what's next (30s)
Ten pain points with evidence, four open questions for the process owner.
"Stage 2 turns these into a diagnosis. Not in this build: per-user Google sign-in, audio transcription, stakeholder-consensus agent."

## Questions to ask them
1. Do the roles and handoffs match how you think about the sales process? Anything mislabelled?
2. Is this the right level — process, not SOP?
3. Which of the ten pain points would you want Stage 2 to go after first?

## Be accurate about the mode
- Mode **live** (cached from last night's run): this is real agent output. Say "this ran last night on these transcripts."
- Mode **replay** (no successful live run yet): steps were computed offline. Say so plainly.
- To run fresh in the meeting, tick *Run agent live*. It takes a couple of minutes, so only do it if there's time.

## If something breaks
Play the recording. Say so plainly: "Here's the run from last night."
