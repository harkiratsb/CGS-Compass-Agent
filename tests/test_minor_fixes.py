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


# ---- M4, M5, M9, M14, M8

STALE = "Written by the AI at run time; may not reflect later answers"


def test_ai_summary_labelled_stale_after_an_answer(demo_model, acceptance_run, settings):
    from framework.outputs import summary_markdown
    run = acceptance_run.model_copy(deep=True)
    run.summary_text, run.first_actions = "AI paragraph", ["one", "two", "three"]
    assert STALE not in summary_markdown(run, demo_model)
    after = answer_and_rescore(run, demo_model, "context.volume", "40", settings)
    md = summary_markdown(after, demo_model)
    assert md.count(STALE) >= 1 and md.index(STALE) < md.index("AI paragraph")


def test_readiness_by_test_lists_the_five_tests(demo_model, acceptance_run, settings):
    from framework.outputs import summary_markdown
    run = acceptance_run.model_copy(deep=True)
    run.steps["A2"].readiness["bus_factor"] = Answer(value=2, basis="answered")
    sect = summary_markdown(run, demo_model).split("## Readiness by test")[1].split("## ")[0]
    lines = [l for l in sect.splitlines() if l.startswith("- ")]
    assert len(lines) == 5
    bus = next(l for l in lines if "Bus factor" in l)
    assert "1 step override below 4" in bus


def test_show_what_changed_keeps_combined_steps(demo_model, all_assumed_run, settings):
    from framework.future import build_future_model
    run = apply_rules(all_assumed_run, demo_model, settings)
    run.steps["A5"].call, run.steps["A5"].combine_target = "Combine", "A4"
    fut, markers = build_future_model(demo_model, run, show_removed=True)
    a5 = next(a for a in fut.activities if a.id == "A5")
    assert markers["A5"] == "removed" and "→ merged into A4" in a5.name
    plain, _ = build_future_model(demo_model, run)
    assert "A5" not in plain.node_ids()


def test_skipped_at_low_gate_note(demo_model, all_assumed_run, settings):
    from framework.outputs import skipped_at_low_gate
    from schema import AgentStep
    run = all_assumed_run.model_copy(deep=True)
    for k in GATE_KEYS:
        run.doc_gate[k] = Answer(value="N", basis="answered")
    run = apply_rules(run, demo_model, settings)
    run.trace = [AgentStep(id="F4", name="Step Removal, pass 1", agent="Step Removal", kind="code",
                           summary="Skipped: documentation confidence is Low (document first).", status="warning")]
    assert run.gate_band == "Low" and not skipped_at_low_gate(run)
    for k in GATE_KEYS:
        run = answer_and_rescore(run, demo_model, f"doc_gate.{k}", "Y", settings)
    assert run.gate_band == "High" and skipped_at_low_gate(run)


def test_screen_text_scrubs_nicknames(demo_model):
    from phase3_ui import _Ctx
    assert "Liz" not in _Ctx(demo_model, ["Elizabeth"]).clean("Liz approves")
