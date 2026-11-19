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


def test_made_unnecessary_by_is_removed(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    run.steps["A5"].call, run.steps["A5"].made_unnecessary_by = "Keep", "A4"
    fut, _ = build_future_model(demo_model, run)
    assert "A5" not in fut.node_ids() and not any(i.startswith("A5.") for i in fut.node_ids())
    assert fut.validate_graph() == []


def test_combine_with_unknown_target_is_treated_as_remove(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    run.steps["A5"].call, run.steps["A5"].combine_target = "Combine", "NOPE"
    fut, _ = build_future_model(demo_model, run)
    assert "A5" not in fut.node_ids() and fut.validate_graph() == []


def test_chain_is_linked_in_order(demo_model, acceptance_run):
    fut, _ = build_future_model(demo_model, acceptance_run)
    pairs = {(e.from_id, e.to_id) for e in fut.edges}
    assert ("A6.1", "A6.2") in pairs
    assert any(t == "A6.1" for _, t in pairs) and not any(f == "A6" or t == "A6" for f, t in pairs)
    original_out = {e.to_id for e in demo_model.edges if e.from_id == "A6"}
    assert original_out and any(f == "A6.2" for f, _ in pairs)
    assert not any(f == "A6.1" and t != "A6.2" for f, t in pairs)


def test_handoffs_and_pain_points_follow_dropped_and_chained_nodes(demo_model, acceptance_run):
    fut, _ = build_future_model(demo_model, acceptance_run)
    ids = fut.node_ids()
    assert all(h.at_node_id in ids for h in fut.handoffs)
    assert all(p.node_id in ids for p in fut.pain_points if p.node_id)
    pains = {p.id: p.node_id for p in fut.pain_points}
    assert "P1" not in pains          # D1 was removed
    assert pains["P2"] == "A6.1"      # A6 became a chain
    assert any(h.at_node_id == "A6.1" for h in fut.handoffs)


def test_future_model_scrubs_names_from_every_label(demo_model, acceptance_run):
    from framework.models import ChainStep
    from render.drawio import render_drawio
    from render.swimlane import build_geometry, render_svg

    run = acceptance_run.model_copy(deep=True)
    model = demo_model.model_copy(deep=True)
    model.roles[0].name = "Elizabeth's team"
    model.decisions[0].question = "Did Liz approve?"
    st = run.steps["A2"]
    st.recommendation.chain = [ChainStep(name="Ask Elizabeth", type="Rules"),
                               ChainStep(name="Enter lead for Liz", type="Human task")]
    fm, markers = build_future_model(model, run, names=["Elizabeth"])
    g = build_geometry(fm)
    for text in (render_svg(fm, markers=markers, geometry=g), render_drawio(fm, g, markers)):
        assert "Elizabeth" not in text and "Liz" not in text
        assert "[team member]" in text
    plain, _ = build_future_model(model, run)          # without names nothing is changed
    assert any("Ask Elizabeth" in a.name for a in plain.activities)
