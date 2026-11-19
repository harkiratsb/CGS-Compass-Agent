"""A step an Agent step makes unnecessary is a Remove everywhere: call, table, waves, summary and diagram.
The AI's mark is kept apart (ai_made_unnecessary_by) so the effective mark is derived fresh every pass."""
from framework.future import build_future_model
from framework.models import Answer
from framework.outputs import step_table, summary_markdown
from framework.rules import answer_and_rescore, apply_rules, undo_answer


def _agentify(run, sid):
    for k in ("A1", "A4"):
        run.steps[sid].agent_test[k] = Answer(value="Y", basis="answered")


def _marked(all_assumed_run, demo_model, settings):
    run = all_assumed_run.model_copy(deep=True)
    _agentify(run, "A7")
    run.steps["A3"].ai_made_unnecessary_by = "A7"
    return apply_rules(run, demo_model, settings)


def _name(model, sid):
    return next(a.name for a in model.activities if a.id == sid)


def test_made_unnecessary_step_is_a_remove_everywhere(all_assumed_run, demo_model, settings):
    base = apply_rules(all_assumed_run, demo_model, settings)
    out = _marked(all_assumed_run, demo_model, settings)
    st = out.steps["A3"]
    assert st.made_unnecessary_by == "A7"
    assert (st.call, st.call_rule) == ("Remove", "Agent")
    assert st.recommendation.action == f"Remove (made unnecessary by {_name(demo_model, 'A7')})"
    assert st.recommendation.wave == 0 and "A3" in out.waves[0]
    assert base.steps["A3"].call == "Keep"
    removed_before = sum(s.call != "Keep" for s in base.steps.values())
    assert sum(s.call != "Keep" for s in out.steps.values()) == removed_before + 1
    row = next(r for r in step_table(out, demo_model) if r["step_id"] == "A3")
    assert row["call"] == "Remove" and row["wave"] == 0 and row["future_chain"] == ""
    assert "A7" in row["reason"] and _name(demo_model, "A7") in row["reason"]
    assert "Not scored (Remove)" in summary_markdown(out, demo_model)
    fut, _ = build_future_model(demo_model, out)
    assert "A3" not in fut.node_ids() and fut.validate_graph() == []
    _, markers = build_future_model(demo_model, out, show_removed=True)
    assert markers["A3"] == "removed"


def test_mark_comes_back_after_undo(all_assumed_run, demo_model, settings):
    out = _marked(all_assumed_run, demo_model, settings)
    path = "steps.A7.agent_test.A4"
    cleared = answer_and_rescore(out, demo_model, path, "N", settings)     # A7 is no longer an Agent step
    assert cleared.steps["A3"].made_unnecessary_by is None and cleared.steps["A3"].call == "Keep"
    assert cleared.steps["A3"].ai_made_unnecessary_by == "A7"
    back = undo_answer(cleared, demo_model, path, settings)
    assert back.steps["A3"].made_unnecessary_by == "A7" and back.steps["A3"].call == "Remove"


def test_older_runs_treat_made_unnecessary_by_as_the_ai_value(all_assumed_run, demo_model, settings):
    run = all_assumed_run.model_copy(deep=True)
    _agentify(run, "A7")
    run.steps["A3"].made_unnecessary_by = "A7"            # saved before ai_made_unnecessary_by existed
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A3"].ai_made_unnecessary_by == "A7" and out.steps["A3"].call == "Remove"


def test_mark_ignored_on_a_step_that_is_not_kept(all_assumed_run, demo_model, settings):
    run = all_assumed_run.model_copy(deep=True)
    _agentify(run, "A7")
    run.steps["A3"].r_pass1["R7"] = Answer(value="Y", basis="answered")   # Fix the map
    run.steps["A3"].ai_made_unnecessary_by = "A7"
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A3"].call == "Fix the map" and out.steps["A3"].made_unnecessary_by is None
