"""Follow-up questions only ask for answers that could change a result, main path first."""
from framework.graph import graph_facts
from framework.models import Answer
from framework.outputs import follow_ups_markdown
from framework.rules import answer_and_rescore, apply_rules, undo_answer
from schema import SourceRef

Q = [SourceRef(file="f", location="1", quote="q")]


def _paths(run, sid):
    return {f.path.split(".", 2)[2] for f in run.follow_ups if f.path.startswith(f"steps.{sid}.")}


def _fams(run, sid):
    return {p.split(".")[0] for p in _paths(run, sid)}


def test_kept_step_with_no_r_yes_skips_r_move_and_combine_with(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    st = run.steps["A11"]
    st.r_move = Answer(value="Remove", question="Move or remove?")
    st.combine_with = Answer(value="A12", question="Combine with?")
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A11"].call == "Keep"
    fams = _fams(out, "A11")
    assert "r_move" not in fams and "combine_with" not in fams
    assert {"r_pass1", "dials", "tier1", "agent_test"} <= fams and "worksheet" not in fams


def test_r4_yes_asks_r_move_and_r3_or_r6_asks_combine_with(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    st = run.steps["A11"]
    st.r_move = Answer(value="Remove", question="Move or remove?")
    st.combine_with = Answer(value="A12", question="Combine with?")
    st.r_pass1["R4"] = Answer(value="Y", basis="stated", quotes=Q)
    assert "r_move" in _fams(apply_rules(run, demo_model, settings), "A11")
    st.r_pass1["R4"] = Answer(value="N", basis="stated", quotes=Q)
    for k in ("R3", "R6"):
        st.r_pass1[k] = Answer(value="Y", basis="stated", quotes=Q)
        assert "combine_with" in _fams(apply_rules(run, demo_model, settings), "A11"), k
        st.r_pass1[k] = Answer(value="N", basis="stated", quotes=Q)


def test_not_kept_step_skips_scoring_families(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    run.steps["A3"].r_pass1["R1"] = Answer(value="Y", basis="stated", quotes=Q)
    run.steps["A3"].readiness["people"] = Answer(value=1, question="People?")
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A3"].call == "Remove"
    fams = _fams(out, "A3")
    assert not fams & {"tier1", "dials", "agent_test", "readiness"}
    assert "r_pass1" in fams and "worksheet" not in fams


def test_made_unnecessary_step_skips_scoring_families(demo_model, acceptance_run, settings):
    # A6 is a kept Agent step in the acceptance run; mark A10 (not protected) unnecessary because of it
    assert acceptance_run.steps["A6"].tier.tier == "Agent"
    run = acceptance_run.model_copy(deep=True)
    run.steps["A10"].made_unnecessary_by = "A6"
    run.steps["A10"].dials["stakes"] = Answer(value=3, question="Stakes?")
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A10"].made_unnecessary_by == "A6"
    assert not _fams(out, "A10") & {"tier1", "dials", "agent_test", "readiness"}


def test_follow_ups_main_path_steps_before_the_rest(demo_model, all_assumed_run, settings):
    out = apply_rules(all_assumed_run, demo_model, settings)
    facts = graph_facts(demo_model, out.success_end)
    off = [s for s in facts.steps if s not in facts.main_path]
    assert off, "the demo map has steps off the main path"
    order = []
    for f in out.follow_ups:
        if f.path.startswith("steps.") and f.path.split(".")[1] not in order:
            order.append(f.path.split(".")[1])
    on = [s for s in order if s in facts.main_path]
    assert order[:len(on)] == on


def test_answer_and_undo_pass_names_to_the_gate(demo_model, all_assumed_run, settings):
    model = demo_model.model_copy(deep=True)
    model.roles[0].name = "Jordyn (Sales)"
    run = apply_rules(all_assumed_run, model, settings, ["Jordyn"])
    assert run.doc_gate["2"].value == "N"
    out = answer_and_rescore(run, model, "context.volume", "40", settings, names=["Jordyn"])
    assert out.doc_gate["2"].value == "N"
    assert undo_answer(out, model, "context.volume", settings, names=["Jordyn"]).doc_gate["2"].value == "N"
    assert answer_and_rescore(run, model, "context.volume", "40", settings).doc_gate["2"].value == "Y"
