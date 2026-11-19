# CGS "Process Improvement Framework: Starter for Team Compass" (2026-11-11)

Working transcription of CGS's spec, made from the screenshots Harkirat shared on 2026-11-18 (lightly condensed).
CGS calls the thresholds a first pass. If the original document is added to the repo, it wins over this file.

## Purpose
Take one documented process (a map, ideally an SOP, such as our Stage 1 output) and recommend a future-state process
as a Lucid diagram. For each step: should it exist, how far should it be automated, and can the business change it
now? Output per-step recommendations and a staged roadmap.

## Ideas
Start with the outcome. Remove before you automate. Digital and standard first. Use the lowest level that gets the
gain. A person stays accountable. Keep the three answers separate. Check calls against the business context, with a
reason and a confidence of H/M/L for each. Missing evidence lowers a score and never raises it.

Step types: Rules (if/then, no AI), Agent, Human approval, Human task.
Tiers: 1 Standardize, 2 Rules, 3 Agentify. Tier 1 comes before 2. Tier 3 can skip 2, and an agent can make groups of
steps unnecessary.

## Part 0 · Documentation gate (Yes = 1, Partly = 0.5)
1. Start and ends marked
2. Lanes are roles
3. Tasks are Verb + Noun
4. Decisions are questions with labelled outcomes
5. Main path and loops shown
6. System named per step
7. Subprocesses linked and owned
8. SOP for human steps

7–8 High: score normally. 4–6.5 Medium: every score "assumed". Under 4 Low: stop and document first.
A missing SOP becomes a "Write the SOP" action for Rules or Agent steps.

## Part 1 · Context
- Outcome
- Goal and measures, with baselines (or "missing")
- A context-read paragraph
- Map check, raised as owner questions, not scored: alternatives drawn as a sequence; a note that is a hidden step;
  a duplicate gate; an SOP/map mismatch; a dead-end path
- Step worksheet: step, who, type today, channel or variant, system, volume and time, notes

## Part 2 · Should it exist (Yes / No / Unknown; Unknown counts as No and is marked "assumed")
- R1 The outcome is reached without it → Remove
- R2 It exists only for an access limit, handoff, info passing, habit or untrusted systems → Remove
- R3 It re-keys existing info → Capture once
- R4 It is mostly waiting → Remove or Move
- R5 It fixes an earlier problem → Remove and fix upstream
- R6 It can merge with an adjacent step by the same actor → Combine
- R7 The map draws two things as one, or alternatives as a sequence → Fix the map (ask the owner)

Decision or accountability steps are kept. Calls: Keep, Remove, Combine, Move, Reimagine, Fix the map.
Pass 1 is as written; pass 2 is a context pass.
Redesign potential = share of steps not Keep: < 10% = 1, 10–24 = 2, 25–39 = 3, 40–59 = 4, 60%+ = 5.

## Part 3 · Tiers (each step stops at the lowest tier that delivers the gain)
**Tier 1:** D1 Digital, D2 Standard (a written way), D3 Findable (named fields or files), each Y / Partly / N.
Anything short of all Y means "Standardize first".

**Tier 2 dials**, 1–5, scored after Tier 1. With no evidence use 3 "assumed", except Rule clarity, which defaults to 1.

| Dial | 1 | 3 | 5 |
|---|---|---|---|
| Rule clarity | nothing written | written, with judgment | fully written |
| Input structure | free text | mix | fields |
| Variation | alike | known variants | each differs |
| Judgment | none | a few factors | context and trade-offs |
| Checkability | must redo | effort | a glance |
| Stakes | undoable, internal | client or money | irreversible, or the relationship is the point |

**Agent test:** A1 needs free text or context; A2 high variation; A3 rules would give about 80% of the gain (if so,
stop at Tier 2); A4 the extra gain is worth it. Pass = (A1 or A2) and not A3 and A4.

**Decision rules, first match wins:**
1. Stakes 5 → Human (an agent may prepare only).
2. Clarity ≥ 4, Input ≥ 4, Variation ≤ 2, Judgment ≤ 2 → Rules.
3. Checkability ≥ 3 and (Judgment ≥ 3 or Input ≤ 2 or Variation ≥ 3) → Agent candidate: Agent if the test passes,
   plus Human approval if Stakes ≥ 3, plus an error budget; otherwise Rules plus a Human task.
4. Judgment ≥ 3 and Checkability ≤ 2 → Human.
5. Otherwise a mixed step: split it, or write the rule down and rescore.
6. No-approver agents only with guardrails, limits and an audit trail.

Loop: rerun Part 2 after Tier 1; after Tier 3, find groups made unnecessary; stop when stable.

## Part 4 · Readiness (1–5, against the planned tier)
| Test | 1 | 3 | 5 |
|---|---|---|---|
| Data and systems | no system of record | structured and owned | one writer with an audit trail, agent-reachable |
| Process clarity | unwritten | mapped with owners, no SOP | map and SOP complete |
| Bus factor | 1 person | 2 people | 3+ people |
| Governance | no owner | named owner, approval place settable | approvals, limits, escalation, error budget, record |
| People | resistance | champions | broad literacy, roles redefined |

Readiness = the lowest of the five, which names what to fix first. No evidence = 1, "unverified". Ready = 4–5.
Below 4 is sequencing, not a stop; a pilot can run meanwhile.

## Part 5 · Recommend
Per step: flagged in Part 2 → change the process (Fix the map → ask the owner and rescore); not digital and standard →
standardize first; pick the lowest tier (Human, Rules or Agent); readiness 4–5 → automate or agentify (approver if
Stakes ≥ 3), else harden first.

Posture, first true line wins: doc confidence Low → Document first. Open map questions on high-flow steps → Fix the
map. Redesign 4–5 → Redesign first. Redesign 3 → redesign the flagged segments, then standardize and automate the rest.
Redesign 1–2 → Incremental. Not ready → add "harden first".

Waves: 0 Fix and remove; 1 Standardize (fails D1–D3); 2 Automate (Rules steps); 3 Agentify (passed the agent test).
No entering a later wave before clearing the earlier one.

Spec per new step:
- Rules: owner, logic, test cases, a run record.
- Agent: goal, systems it reads and writes, never-dos, handoff to a person, logs, error budget, failure modes, down
  behaviour.
- Human approval: authority, what they see, what they decide, speed, record.
- Human task: what they do, skills, why it stays human.

## Deliverables
The main one is a Lucid diagram of the future state, with removed or combined steps gone and each remaining step typed
(a step often becomes a chain like Agent → Human approval → Rules). Behind it: a step table (step, call, tier result,
one-line reason plus confidence; the diagram is drawn from it) and a summary (outcome and goal, doc confidence, open map
questions, redesign potential, steps by tier, readiness by test, posture, waves, first three actions). Mark missing
evidence as a gap and don't guess. Optional: the doc agent could later draw the diagram from the typed step table.

## Worked example (CGS, our sales process)
- Check CRM access before entering a lead = Remove (logins differ).
- Enter the lead in the CRM = Keep; standardize first, then Rules (intake form → fields → a rule creates the record).
- Contact the lead using a sales play = Keep; Agent with approval (free-text drafting, external first contact).
- Meet the lead = Keep; Human (the relationship is the point, highest stakes).
- Across 28 steps, 17 of the 25 that remained ended at Rules or Human.

## Still being refined (per CGS)
The thresholds, the Tier 1 tests, the agent test, and the room for the context pass. CGS wants feedback from real runs.
