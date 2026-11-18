from framework.graph import graph_facts
from framework.models import (
    GATE_KEYS, READINESS_KEYS, Answer, MapCheck, Recommendation, StepAssessment, TierResult,
    blank_run, get_answer,
)
from framework.rules import apply_rules, posture, readiness_min, recommend, waves
from ingest.merge import source_coverage
from schema import SourceRef


def test_readiness_min_uses_lowest_and_defaults(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, readiness={"bus_factor": Answer(value=2, basis="stated", derived=True)})
    defaults = {k: Answer(value=5, basis="stated", derived=True) for k in READINESS_KEYS}
    assert readiness_min(st, defaults, settings) == 2
    assert readiness_min(StepAssessment(step_id="Y", worksheet={}, r_pass1={}), {}, settings) == 1


def test_recommend_waves(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, call="Keep", readiness_min=4,
                        tier=TierResult(standardize_first=True, tier="Rules", rule_fired=2))
    r = recommend(st, settings)
    assert r.wave == 1 and "Rules" in r.action and not r.harden_first and [c.type for c in r.chain] == ["Rules"]
    st.call = "Remove"; assert recommend(st, settings).wave == 0


def test_recommend_fix_the_map_and_tier_waves(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, call="Fix the map", readiness_min=5,
                        tier=TierResult(standardize_first=False, tier="Rules", rule_fired=2))
    r = recommend(st, settings)
    assert (r.action, r.wave) == ("Ask the owner, then rescore", 0)
    st.call = "Keep"
    assert recommend(st, settings).wave == 2
    st.tier = TierResult(standardize_first=False, tier="Human", rule_fired=1)
    r = recommend(st, settings)
    assert r.wave is None and [c.type for c in r.chain] == ["Human task"]


def test_harden_first_when_not_ready(settings):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={}, call="Keep", readiness_min=2,
                        tier=TierResult(standardize_first=False, tier="Agent", approval=True, rule_fired=3))
    r = recommend(st, settings)
    assert r.harden_first and [c.type for c in r.chain] == ["Agent", "Human approval"]


def test_blank_run_defaults_and_prefill(demo_model):
    run = blank_run(demo_model, "fixture")
    facts = graph_facts(demo_model)
    assert list(run.steps) == facts.steps and run.success_end == facts.success_end and not run.map_reviewed
    assert all(run.doc_gate[k].value == "N" and run.doc_gate[k].question for k in GATE_KEYS)
    st = run.steps["A2"]
    assert st.dials["rule_clarity"].value == 1 and st.dials["stakes"].value == 3
    assert st.r_pass1["R1"].value == "Unknown" and st.tier1["D1"].value == "N" and st.agent_test["A4"].value == "N"
    assert st.readiness == {} and run.readiness_defaults["governance"].value == 1
    sys_ = st.worksheet["system"]
    assert (sys_.value, sys_.basis, sys_.derived) == ("Insightly", "stated", True)
    assert st.worksheet["minutes"].value == 10.0 and st.worksheet["type_today"].value == "Rules"
    assert run.steps["D1"].worksheet["type_today"].value == "Human task"
    assert run.steps["A1"].worksheet["system"].basis == "assumed"
    assert all(a.question for st in run.steps.values() for fam in (st.dials, st.r_pass1, st.tier1)
               for a in fam.values())


def test_apply_rules_low_gate_stops_tiers(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    for k in GATE_KEYS: run.doc_gate[k] = Answer(value="N", basis="answered")
    out = apply_rules(run, demo_model, settings)
    assert out.gate_band == "Low" and out.posture[0] == "Document first"
    assert all(st.tier is None for st in out.steps.values())


def test_apply_rules_never_overwrites_answered(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True); run.doc_gate["6"] = Answer(value="Y", basis="answered")
    assert apply_rules(run, demo_model, settings).doc_gate["6"].value == "Y"


def test_apply_rules_is_pure(demo_model, all_assumed_run, settings):
    before = all_assumed_run.model_dump()
    out = apply_rules(all_assumed_run, demo_model, settings)
    assert all_assumed_run.model_dump() == before and out is not all_assumed_run


def test_single_source_caps_confidence(demo_model, all_assumed_run, settings):
    cov = source_coverage(demo_model)
    single = next(s for s in all_assumed_run.steps if cov.get(s, 0) < 2)
    run = all_assumed_run.model_copy(deep=True)
    run.steps[single].dials["stakes"] = Answer(value=4, basis="stated", confidence="H", quotes=[SourceRef(file="f", location="1", quote="q")])
    assert apply_rules(run, demo_model, settings).steps[single].dials["stakes"].confidence == "M"


def _medium_run(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    for k, v in zip(GATE_KEYS, ["Y", "Y", "Y", "Y", "N", "N", "N", "N"]):
        run.doc_gate[k] = Answer(value=v, basis="answered")
    return run


def test_medium_gate_flags_and_caps(demo_model, all_assumed_run, settings):
    run = _medium_run(all_assumed_run)
    q = [SourceRef(file="a", location="1", quote="q")]
    run.steps["A2"].dials["stakes"] = Answer(value=2, basis="stated", confidence="H", quotes=q)
    run.steps["A2"].dials["judgment"] = Answer(value=1, basis="answered", confidence="H")
    out = apply_rules(run, demo_model, settings)
    assert out.gate_band == "Medium"
    stakes, judgment = out.steps["A2"].dials["stakes"], out.steps["A2"].dials["judgment"]
    assert stakes.value == 2 and stakes.confidence == "M" and "doc confidence Medium" in stakes.flags
    assert judgment.confidence == "H" and "doc confidence Medium" not in judgment.flags
    again = apply_rules(out, demo_model, settings)
    assert again.steps["A2"].dials["stakes"].flags.count("doc confidence Medium") == 1
    assert all(st.tier is not None and st.recommendation is not None for st in out.steps.values())


def test_write_the_sop_on_automated_steps(demo_model, all_assumed_run, settings):
    run = _medium_run(all_assumed_run)
    st = run.steps["A2"]
    for k, v in dict(rule_clarity=5, input_structure=5, variation=1, judgment=1, checkability=5, stakes=2).items():
        st.dials[k] = Answer(value=v, basis="answered")
    out = apply_rules(run, demo_model, settings)
    assert out.steps["A2"].tier.tier == "Rules" and "Write the SOP" in out.steps["A2"].recommendation.action
    run.doc_gate["8"] = Answer(value="Y", basis="answered")
    assert "Write the SOP" not in apply_rules(run, demo_model, settings).steps["A2"].recommendation.action


def test_posture_lines(demo_model, all_assumed_run, settings):
    facts = graph_facts(demo_model)
    run = all_assumed_run.model_copy(deep=True)
    run.gate_band, run.redesign_potential = "High", 4
    assert posture(run, facts) == ["Redesign first"]
    run.redesign_potential = 3
    assert posture(run, facts) == ["Redesign the flagged segments, then standardize and automate the rest"]
    run.redesign_potential = 2
    run.steps["A2"].tier = TierResult(standardize_first=False, tier="Rules", rule_fired=2)
    run.steps["A2"].recommendation = Recommendation(action="Rules", wave=2, harden_first=True)
    assert posture(run, facts) == ["Incremental", "Harden first"]
    run.map_checks = [MapCheck(type="dead_end", step_ids=[next(iter(facts.main_path))], question="?")]
    assert posture(run, facts)[0] == "Fix the map"
    run.gate_band = "Low"
    assert posture(run, facts)[0] == "Document first"


def test_waves_group_steps(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    run.steps["A1"].recommendation = Recommendation(action="Remove", wave=0)
    run.steps["A2"].recommendation = Recommendation(action="Rules", wave=2)
    run.steps["A3"].recommendation = Recommendation(action="Human", wave=None)
    w = waves(run)
    assert set(w) == {0, 1, 2, 3} and w[0] == ["A1"] and w[2] == ["A2"] and w[1] == w[3] == []


def test_follow_ups_list_every_assumed(demo_model, all_assumed_run, settings):
    out = apply_rules(all_assumed_run, demo_model, settings)
    assert out.follow_ups and out.follow_ups[0].path.split(".")[0] in ("doc_gate", "context", "readiness_defaults")
    assert all(get_answer(out, f.path).basis == "assumed" for f in out.follow_ups)
    assert all(f.question for f in out.follow_ups)
    step_ids = [f.path.split(".")[1] for f in out.follow_ups if f.path.startswith("steps.")]
    order = graph_facts(demo_model).steps
    assert step_ids == sorted(step_ids, key=order.index)
