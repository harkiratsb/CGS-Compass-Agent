import csv
import io

from framework.outputs import (STEP_TABLE_COLUMNS, follow_ups_markdown, step_table,
                               step_table_csv, summary_markdown)
from framework.rules import apply_rules


def test_step_table_rows(demo_model, acceptance_run):
    rows = step_table(acceptance_run, demo_model)
    assert list(rows[0]) == STEP_TABLE_COLUMNS
    a6 = next(r for r in rows if r["step_id"] == "A6")
    assert a6["call"] == "Keep" and a6["tier_result"] == "Agent + Human approval" and a6["future_chain"] == "Agent → Human approval"
    assert step_table_csv(rows).splitlines()[0] == ",".join(STEP_TABLE_COLUMNS)
    assert len(rows) == len(acceptance_run.steps)


def test_standardize_first_and_removed_rows(demo_model, acceptance_run):
    rows = {r["step_id"]: r for r in step_table(acceptance_run, demo_model)}
    assert rows["A2"]["tier_result"] == "Standardize first → Rules"
    assert rows["A2"]["reason"].startswith("Tier rule 2")
    assert rows["A2"]["wave"] == 1 and rows["A2"]["gaps"] > 0
    assert rows["A3"]["call"] == "Remove" and rows["A3"]["future_chain"] == ""
    assert rows["A3"]["reason"].startswith("R")
    parsed = list(csv.DictReader(io.StringIO(step_table_csv(list(rows.values())))))
    assert len(parsed) == len(rows)


def test_confidence_is_lowest_of_deciding_answers(demo_model, acceptance_run):
    acceptance_run.steps["A2"].dials["stakes"].confidence = "M"
    for a in acceptance_run.steps["A2"].dials.values():
        if a.confidence == "L":
            a.confidence = "H"
    for a in acceptance_run.steps["A2"].tier1.values():
        a.confidence = "H"
    row = next(r for r in step_table(acceptance_run, demo_model) if r["step_id"] == "A2")
    assert row["confidence"] == "M"


def test_summary_sections_and_gaps(demo_model, all_assumed_run, settings):
    md = summary_markdown(apply_rules(all_assumed_run, demo_model, settings), demo_model)
    heads = [l[3:] for l in md.splitlines() if l.startswith("## ")]
    assert heads == ["Outcome and goal", "Documentation confidence", "Open map questions", "Redesign potential",
                     "Steps by tier", "Readiness by test", "Posture", "Waves", "First three actions", "Gaps"]
    assert "Gap:" in md


def test_names_removed(demo_model, acceptance_run):
    acceptance_run.summary_text = "Liz reviews it"
    out = summary_markdown(acceptance_run, demo_model, names=["Liz"])
    assert "Liz" not in out and "[team member]" in out
    acceptance_run.follow_ups[0].question = "Does Elizabeth approve?"
    assert "Elizabeth" not in follow_ups_markdown(acceptance_run, demo_model, names=["Elizabeth"])
    assert "Liz" not in follow_ups_markdown(acceptance_run, demo_model, names=["Elizabeth"])


def test_follow_ups_process_first_and_deduped(demo_model, acceptance_run):
    md = follow_ups_markdown(acceptance_run, demo_model)
    assert md.index("## Start here") < md.index("### A1")
    if "## Other steps" in md:
        assert md.index("## Steps on the main path") < md.index("## Other steps")
    qs = [l for l in md.splitlines() if l.startswith("- ")]
    assert len(qs) == len(set(qs))


def test_reason_uses_rule_that_fired_not_first_yes(demo_model, acceptance_run, settings):
    st = acceptance_run.steps["A5"]
    for k in ("R1", "R2", "R3", "R4", "R5"):
        st.r_pass1[k].value = "N"
        st.r_pass2.pop(k, None)
    st.r_pass1["R6"].value = "Y"
    st.r_pass1["R6"].reason = "merge reason"
    st.r_pass1["R7"].value = "Y"
    st.r_pass1["R7"].reason = "map reason"
    st.combine_with = None  # R6 is skipped in part2_call
    run = apply_rules(acceptance_run, demo_model, settings)
    row = next(r for r in step_table(run, demo_model) if r["step_id"] == "A5")
    assert row["call"] == "Fix the map" and row["reason"] == "R7: map reason"


def test_removed_row_is_wave_zero_and_empty_reason_is_gap(demo_model, acceptance_run):
    row = next(r for r in step_table(acceptance_run, demo_model) if r["step_id"] == "A3")
    assert row["call"] == "Remove" and row["wave"] == 0
    acceptance_run.steps["A3"].r_pass1[acceptance_run.steps["A3"].call_rule].reason = ""
    acceptance_run.steps["A3"].r_pass2.clear()
    row = next(r for r in step_table(acceptance_run, demo_model) if r["step_id"] == "A3")
    assert row["reason"] == "Gap: no reason recorded"
