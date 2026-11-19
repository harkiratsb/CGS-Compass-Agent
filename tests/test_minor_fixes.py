"""Small fixes from the final review (M1, M2, M3, M6)."""
import pytest

from framework.models import GATE_KEYS, Answer, ChainStep, allowed_values
from framework.rules import answer_and_rescore, apply_rules, chain_compatible, undo_answer
from schema import SourceRef

Q = [SourceRef(file="f", location="1", quote="q")]


def test_undo_puts_flags_back(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    run.context["volume"].flags.append("invalid AI value")
    back = undo_answer(answer_and_rescore(run, demo_model, "context.volume", "40", settings),
                       demo_model, "context.volume", settings)
    assert "invalid AI value" in back.context["volume"].flags


def test_ai_chain_may_not_keep_a_type_code_no_longer_chose():
    code = [ChainStep(name="Agent", type="Agent")]
    with_approval = [ChainStep(name="Draft", type="Agent"), ChainStep(name="Approve", type="Human approval")]
    assert not chain_compatible(with_approval, code)
    assert chain_compatible([ChainStep(name="Draft", type="Agent"), ChainStep(name="Check", type="Human task")], code)
    assert not chain_compatible([ChainStep(name="R", type="Rules"), ChainStep(name="D", type="Agent")], code)
    assert chain_compatible(with_approval, [ChainStep(name="a", type="Agent"), ChainStep(name="b", type="Human approval")])


def test_medium_flag_and_cap_reach_process_level_answers(demo_model, all_assumed_run, settings):
    run = all_assumed_run.model_copy(deep=True)
    for k, v in zip(GATE_KEYS, ["Y", "Y", "Y", "Y", "N", "N", "N", "N"]):
        run.doc_gate[k] = Answer(value=v, basis="answered")
    run.context["outcome"] = Answer(value="x", basis="stated", confidence="H", quotes=Q)
    run.readiness_defaults["people"] = Answer(value=4, basis="stated", confidence="H", quotes=Q)
    out = apply_rules(run, demo_model, settings)
    for a in (out.context["outcome"], out.readiness_defaults["people"]):
        assert a.confidence == "M" and "doc confidence Medium" in a.flags
    again = apply_rules(out, demo_model, settings)
    assert again.context["outcome"].flags.count("doc confidence Medium") == 1
    for k in GATE_KEYS:
        again.doc_gate[k] = Answer(value="Y", basis="answered")
    high = apply_rules(again, demo_model, settings)               # reversible: the gate rises to High
    assert high.context["outcome"].confidence == "H" and "doc confidence Medium" not in high.context["outcome"].flags


def test_combine_with_takes_another_step_id(demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    ids = list(run.steps)
    assert allowed_values("steps.A2.combine_with", ids) == tuple(i for i in ids if i != "A2")
    for bad in ("A2", "nope"):
        with pytest.raises(ValueError):
            answer_and_rescore(run, demo_model, "steps.A2.combine_with", bad, settings)
    assert answer_and_rescore(run, demo_model, "steps.A2.combine_with", "A1", settings).steps["A2"].combine_with.value == "A1"
