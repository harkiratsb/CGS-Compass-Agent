import pytest

from framework.models import A_KEYS, D_KEYS, Answer, StepAssessment
from framework.rules import agent_test_pass, dial, tier_decision


def _t(d=("Y", "Y", "Y"), a=("Y", "N", "N", "Y"), **dials):
    st = StepAssessment(step_id="X", worksheet={}, r_pass1={})
    st.tier1 = {k: Answer(value=v, basis="stated", derived=True) for k, v in zip(D_KEYS, d)}
    st.agent_test = {k: Answer(value=v, basis="stated", derived=True) for k, v in zip(A_KEYS, a)}
    st.dials = {k: Answer(value=v, basis="stated", derived=True) for k, v in dials.items()}
    return st


CASES = [
    (dict(stakes=5, rule_clarity=5, input_structure=5, variation=1, judgment=1, checkability=5), ("Human", 1, False)),
    (dict(stakes=2, rule_clarity=4, input_structure=4, variation=2, judgment=1, checkability=5), ("Rules", 2, False)),
    (dict(stakes=3, rule_clarity=2, input_structure=2, variation=3, judgment=3, checkability=4), ("Agent", 3, True)),
    (dict(stakes=2, rule_clarity=2, input_structure=3, variation=2, judgment=4, checkability=2), ("Human", 4, False)),
    (dict(stakes=2, rule_clarity=3, input_structure=3, variation=2, judgment=2, checkability=2), ("Mixed", 5, False)),
]


@pytest.mark.parametrize("dials,expected", CASES)
def test_tier_decision(dials, expected, settings):
    r = tier_decision(_t(**dials), settings)
    assert (r.tier, r.rule_fired, r.approval) == expected


def test_agent_gets_error_budget(settings):
    r = tier_decision(_t(**CASES[2][0]), settings)
    assert r.error_budget and not r.human_task


def test_agent_test_fail_gives_rules_plus_human_task(settings):
    r = tier_decision(_t(a=("Y", "N", "Y", "Y"), stakes=2, rule_clarity=2, input_structure=2, variation=3, judgment=3, checkability=4), settings)
    assert (r.tier, r.human_task) == ("Rules", True)


def test_standardize_first(settings):
    assert tier_decision(_t(d=("Y", "Partly", "Y"), stakes=2, rule_clarity=4, input_structure=4, variation=1, judgment=1, checkability=5), settings).standardize_first
    assert not tier_decision(_t(stakes=2, rule_clarity=4, input_structure=4, variation=1, judgment=1, checkability=5), settings).standardize_first


def test_agent_test_pass():
    def at(*v):
        return {k: Answer(value=x) for k, x in zip(A_KEYS, v)}
    assert agent_test_pass(at("Y", "N", "N", "Y"))
    assert agent_test_pass(at("N", "Y", "N", "Y"))
    assert not agent_test_pass(at("N", "N", "N", "Y"))
    assert not agent_test_pass(at("Y", "N", "Y", "Y"))
    assert not agent_test_pass(at("Y", "N", "N", "Unknown"))
    assert not agent_test_pass({})


def test_dial_defaults(settings):
    st = _t()
    assert dial(st, "rule_clarity", settings) == 1 and dial(st, "variation", settings) == 3
    st.dials["variation"] = Answer(value=9, basis="stated", derived=True)
    assert dial(st, "variation", settings) == 3


@pytest.mark.parametrize("raw,expected", [("4", 4), (" 2 ", 2), (True, 3), (0, 3), ("x", 3), (4.0, 4), ("4.0", 4), (3.5, 3), ("3.5", 3), ("²", 3), ("nan", 3), ("inf", 3), (float("nan"), 3), (None, 3), ("6", 3)])
def test_dial_coercion(raw, expected, settings):
    st = _t()
    st.dials["variation"] = Answer(value=raw)
    assert dial(st, "variation", settings) == expected


def _r(**kw):
    base = dict(stakes=2, rule_clarity=4, input_structure=4, variation=2, judgment=2, checkability=5)
    base.update(kw)
    return base


@pytest.mark.parametrize("dials,expected", [
    (_r(rule_clarity=3), ("Mixed", 5)),   # clarity just below the Rules minimum
    (_r(rule_clarity=4), ("Rules", 2)),
    (_r(stakes=4), ("Rules", 2)),         # stakes just below Human
    (_r(stakes=5), ("Human", 1)),
    # rule 3 OR: only input_structure <= 2 fires
    (dict(stakes=2, rule_clarity=2, input_structure=2, variation=2, judgment=2, checkability=3), ("Agent", 3)),
    # rule 3 OR: only variation >= 3 fires
    (dict(stakes=2, rule_clarity=2, input_structure=3, variation=3, judgment=2, checkability=3), ("Agent", 3)),
    # none of the OR terms: falls through
    (dict(stakes=2, rule_clarity=2, input_structure=3, variation=2, judgment=2, checkability=3), ("Mixed", 5)),
])
def test_boundaries(dials, expected, settings):
    r = tier_decision(_t(**dials), settings)
    assert (r.tier, r.rule_fired) == expected
