"""Behaviour follows settings.toml: change one threshold in a temp copy and the result moves."""
from framework.models import Answer, StepAssessment
from framework.rules import score_gate, tier_decision
from framework.settings import load_settings
from tests.conftest import ROOT


def _with(tmp_path, old, new):
    text = (ROOT / "framework/settings.toml").read_text()
    assert old in text
    p = tmp_path / "s.toml"
    p.write_text(text.replace(old, new))
    return load_settings(p)


def _ans(v):
    return Answer(value=v, basis="stated", derived=True)


def test_gate_high_moves_the_band(tmp_path):
    answers = {f"G{i}": _ans("Y") for i in range(7)}   # 7 points
    assert score_gate(answers, load_settings()) == (7.0, "High")
    assert score_gate(answers, _with(tmp_path, "high = 7.0", "high = 8.0")) == (7.0, "Medium")


def test_gate_medium_moves_the_band(tmp_path):
    answers = {f"G{i}": _ans("Y") for i in range(4)}   # 4 points
    assert score_gate(answers, load_settings())[1] == "Medium"
    assert score_gate(answers, _with(tmp_path, "medium = 4.0", "medium = 5.0"))[1] == "Low"


def _rules_step():
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={})
    st.tier1 = {k: _ans("Y") for k in ("D1", "D2", "D3")}
    st.dials = {k: _ans(v) for k, v in dict(stakes=2, rule_clarity=4, input_structure=4,
                                             variation=2, judgment=2, checkability=2).items()}
    return st


def test_tier_clarity_min_moves_rules_to_something_else(tmp_path):
    st = _rules_step()
    t = tier_decision(st, load_settings())
    assert (t.tier, t.rule_fired) == ("Rules", 2)
    t = tier_decision(st, _with(tmp_path, "clarity_min = 4", "clarity_min = 5"))
    assert (t.tier, t.rule_fired) != ("Rules", 2)


def test_tier_human_stakes_moves_rule_1(tmp_path):
    st = _rules_step()
    st.dials["stakes"] = _ans(4)
    assert tier_decision(st, load_settings()).rule_fired != 1
    assert tier_decision(st, _with(tmp_path, "stakes = 5", "stakes = 4")).rule_fired == 1
