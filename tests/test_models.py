import pytest

from framework.models import (
    SCORE, YN, YNU, YPN, Answer, FrameworkRun, StepAssessment, allowed_values,
    assumed, get_answer,
)


def test_stated_without_quote_becomes_assumed():
    a = Answer(value="Y", basis="stated")
    assert a.basis == "assumed" and "no quote" in a.flags


def test_derived_stated_needs_no_quote():
    assert Answer(value="Y", basis="stated", derived=True).basis == "stated"


def test_assumed_helper():
    a = assumed(3, "How varied are the inputs?")
    assert (a.value, a.basis, a.confidence, a.question) == (3, "assumed", "L", "How varied are the inputs?")


def test_get_answer_and_allowed_values(minimal_run):
    assert get_answer(minimal_run, "steps.A2.dials.stakes").value == 3
    assert allowed_values("steps.A2.dials.stakes") == SCORE
    assert allowed_values("steps.A2.r_pass1.R4") == YNU
    assert allowed_values("doc_gate.8") == YPN
    with pytest.raises(KeyError):
        get_answer(minimal_run, "steps.ZZ.dials.stakes")


def test_get_answer_all_path_families(minimal_run):
    r = minimal_run
    r.doc_gate["8"] = assumed("Y", "q")
    r.context["volume"] = assumed(10, "q")
    r.readiness_defaults["people"] = assumed(1, "q")
    s = r.steps["A2"]
    s.r_pass1["R4"] = assumed("N", "q")
    s.r_move = assumed("Move", "q")
    s.accountability = assumed("Y", "q")
    s.combine_with = assumed("A3", "q")
    s.worksheet["system"] = assumed("SAP", "q")
    s.readiness["bus_factor"] = assumed(2, "q")
    for path, val in [
        ("doc_gate.8", "Y"), ("context.volume", 10), ("readiness_defaults.people", 1),
        ("steps.A2.r_pass1.R4", "N"), ("steps.A2.r_move", "Move"),
        ("steps.A2.accountability", "Y"), ("steps.A2.combine_with", "A3"),
        ("steps.A2.worksheet.system", "SAP"), ("steps.A2.readiness.bus_factor", 2),
    ]:
        assert get_answer(r, path).value == val


@pytest.mark.parametrize("path", [
    "doc_gate.9", "context.nope", "steps.A2.r_move", "steps.A2.bogus.x",
    "steps.A2.dials", "nothing.x", "steps.A2.r_pass2.R1", "",
])
def test_get_answer_missing_raises(minimal_run, path):
    with pytest.raises(KeyError):
        get_answer(minimal_run, path)


def test_allowed_values_families():
    assert allowed_values("steps.A2.r_pass2.R1") == YNU
    assert allowed_values("steps.A2.r_move") == ("Move", "Remove")
    assert allowed_values("steps.A2.accountability") == YN
    assert allowed_values("steps.A2.combine_with") is None
    assert allowed_values("steps.A2.tier1.D1") == YPN
    assert allowed_values("steps.A2.agent_test.A1") == YN
    assert allowed_values("readiness_defaults.people") == SCORE
    assert allowed_values("steps.A2.readiness.people") == SCORE
    assert allowed_values("context.volume") is None
    assert allowed_values("steps.A2.worksheet.minutes") is None


def test_mutable_defaults_not_shared():
    a, b = Answer(), Answer()
    a.flags.append("x")
    assert b.flags == []
    s1 = StepAssessment(step_id="1", worksheet={}, r_pass1={})
    s2 = StepAssessment(step_id="2", worksheet={}, r_pass1={})
    s1.dials["x"] = assumed(3, "q")
    assert s2.dials == {}
    assert FrameworkRun(mode="fixture", map_reviewed=False, doc_gate={}, context={},
                        readiness_defaults={}, steps={}).artifacts == {}
