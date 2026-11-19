"""Live check: run the Process Improvement Framework on the demo map with the real API and compare
the result with the CGS worked-example verdicts (tests/test_acceptance.py).

    .venv/bin/python scripts/phase3_live_check.py            # report only
    .venv/bin/python scripts/phase3_live_check.py --record   # also write the demo replay

The demo state is built the way the app builds it: demo materials -> shipped Stage 1 replay ->
dedupe -> process name and owner from the app's intake defaults. The replay only matches in the
app when that map is unchanged. Agreement is reported, not enforced: the exit code is always 0.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from framework.agent import DEMO_RUN, run_framework  # noqa: E402
from framework.demo import demo_state  # noqa: E402
from framework.models import R_KEYS, FrameworkRun, StepAssessment  # noqa: E402
from framework.rules import r_answer  # noqa: E402
from handoff import speaker_names  # noqa: E402


def _fmt(answers) -> str:
    return ", ".join(f"{k}={a.value} ({a.basis})" for k, a in answers.items())


def _deciding(st: StepAssessment) -> str:
    if st.call != "Keep":
        # the R answers in force (pass 2 where it overrides pass 1), so every key shows
        r = {k: a for k in R_KEYS if (a := r_answer(st, k)) is not None}
        extra = f"; r_move={st.r_move.value}" if st.r_move else ""
        return f"call {st.call} by {st.call_rule or '-'}; R: {_fmt(r)}{extra}"
    t = st.tier
    tier = "not scored" if t is None else (f"tier {t.tier} (rule {t.rule_fired}), approval={t.approval}, "
                                           f"standardize_first={t.standardize_first}")
    return f"call Keep; {tier}; dials: {_fmt(st.dials)}; D: {_fmt(st.tier1)}; A: {_fmt(st.agent_test)}"


def _checks(steps: dict[str, StepAssessment]):
    """The Task 14 verdicts: (label, step ids, predicate on a step)."""
    def tier(st):
        return st.tier.tier if st.call == "Keep" and st.tier else None
    return [
        ("Remove", ["D1", "D5", "D8"], lambda st: st.call == "Remove"),
        ("Keep, standardize first, Rules", ["A2"],
         lambda st: tier(st) == "Rules" and st.tier.standardize_first),
        ("Keep, Agent with approval", ["A6"], lambda st: tier(st) == "Agent" and st.tier.approval),
        ("Keep, Human", ["A11"], lambda st: tier(st) == "Human"),
        ("Remove or Combine", ["A3", "A4"], lambda st: st.call in ("Remove", "Combine")),
    ]


def report(run: FrameworkRun) -> None:
    reached = differ = 0
    for label, ids, ok in _checks(run.steps):
        for sid in ids:
            st = run.steps.get(sid)
            if st is None:
                print(f"  ?  {sid}: expected {label}; step not in the run")
                differ += 1
            elif ok(st):
                print(f"  OK {sid}: {label}")
                reached += 1
            else:
                print(f"  -- {sid}: expected {label}; got {_deciding(st)}")
                differ += 1
    print(f"\n{reached} of {reached + differ} CGS verdicts reached, {differ} differ.")
    kept = [s for s in run.steps.values() if s.call == "Keep" and s.tier]
    share = sum(s.tier.tier in ("Rules", "Human") for s in kept)
    print(f"{share} of {len(kept)} kept steps end at Rules or Human (CGS: 17 of 25 on their 28-step map)")
    print(f"Documentation {run.gate_band} (score {run.gate_score:g}); redesign potential {run.redesign_potential}; "
          f"{len(run.follow_ups)} follow-up questions")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", action="store_true", help=f"write the run to {DEMO_RUN.relative_to(ROOT)}")
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY set; the live check needs the API. Nothing run.")
        return 0
    files, parsed, agent_run, model = demo_state()
    t0 = time.time()

    def on_step(stp):
        flag = "!" if stp.status != "ok" else " "
        print(f"{flag} {stp.id} {stp.name} ({stp.model or 'code'}, {stp.duration_s or 0}s): {stp.summary}", flush=True)

    try:
        run = run_framework(model, parsed, agent_run=agent_run, map_reviewed=True, force_live=True,
                            progress=on_step, names=speaker_names(files))
    except Exception as e:  # noqa: BLE001 - reported, not enforced
        print(f"Live run failed: {e}")
        return 0
    print(f"\nLive run finished in {time.time() - t0:.0f}s.\n")
    report(run)
    failed = [s.name for s in run.trace if s.status != "ok" and s.kind == "llm"]
    if args.record:
        if failed:
            print(f"\nNot recorded: AI step(s) failed ({', '.join(failed)}); a replay must be a complete run.")
        else:
            DEMO_RUN.write_text(run.model_dump_json(indent=2))
            print(f"\nRecorded the replay to {DEMO_RUN.relative_to(ROOT)} (input hash {run.artifacts['input_hash']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
