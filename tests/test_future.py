import json

import pytest

from framework.future import AUTOMATION_ROLE_ID, build_future_model
from framework.models import blank_run, load_answers
from framework.rules import apply_rules


def test_future_drops_removed_and_types_chains(demo_model, acceptance_run):
    fut, markers = build_future_model(demo_model, acceptance_run)
    ids = fut.node_ids()
    assert "D1" not in ids and fut.validate_graph() == []
    assert markers["A6.1"] == "agent" and markers["A6.2"] == "approval"
    assert next(a for a in fut.activities if a.id == "A6.1").role_id == AUTOMATION_ROLE_ID
    assert any(r.id == AUTOMATION_ROLE_ID for r in fut.roles)
    assert fut.process.name == demo_model.process.name


def test_remove_chain_of_steps_keeps_graph_valid(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    for sid in ("A2", "A3", "A4", "D1"):
        run.steps[sid].call = "Remove"
    fut, _ = build_future_model(demo_model, run)
    assert fut.validate_graph() == [] and not {"A2", "A3", "A4", "D1"} & fut.node_ids()
    # A1's flow used to enter D1; it now reaches a kept node instead
    out = [e.to_id for e in fut.edges if e.from_id.split(".")[0] == "A1"]
    assert out and set(out) <= fut.node_ids()


def test_removed_decision_repoints_to_main_path_branch(demo_model, acceptance_run):
    fut, _ = build_future_model(demo_model, acceptance_run)
    into = [e.to_id for e in fut.edges if e.from_id.split(".")[0] == "A1"]
    assert into and set(into) <= fut.node_ids()
    assert "A2" in {t.split(".")[0] for t in into}  # D1's Yes branch (the main path)


def test_show_removed_keeps_greyed_nodes(demo_model, acceptance_run):
    fut, markers = build_future_model(demo_model, acceptance_run, show_removed=True)
    assert markers["D1"] == "removed" and "D1" in fut.node_ids()
    assert fut.validate_graph() == []


def test_combine_merges_tools_and_refs_into_target(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    run.steps["A3"].call, run.steps["A3"].combine_target = "Combine", "A4"
    fut, _ = build_future_model(demo_model, run)
    a3 = next(a for a in demo_model.activities if a.id == "A3")
    a4 = next(a for a in fut.activities if a.id.split(".")[0] == "A4")
    assert "A3" not in fut.node_ids() and set(a3.tools) <= set(a4.tools)
    assert fut.validate_graph() == []


def test_input_model_is_not_mutated(demo_model, acceptance_run):
    before = demo_model.model_dump_json()
    build_future_model(demo_model, acceptance_run)
    assert demo_model.model_dump_json() == before


def test_load_answers_unknown_step_raises(demo_model, tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"steps.NOPE.r_pass1.R2": {"value": "Y"}}))
    with pytest.raises(KeyError):
        load_answers(blank_run(demo_model, "fixture"), p)


def test_load_answers_sets_values_and_leaves_input(demo_model, tmp_path):
    base = blank_run(demo_model, "fixture")
    p = tmp_path / "a.json"
    p.write_text(json.dumps({
        "steps.A11.accountability": {"value": "Y", "basis": "assumed"},
        "steps.A2.dials.stakes": {"value": 2, "basis": "assumed"}}))
    run = load_answers(base, p)
    assert run.steps["A11"].accountability.value == "Y" and run.steps["A2"].dials["stakes"].value == 2
    assert base.steps["A11"].accountability is None
