import pytest

from framework.models import StepAssessment
from framework.rules import answer_and_rescore, apply_rules, undo_answer


@pytest.fixture
def base(demo_model, all_assumed_run, settings):
    return apply_rules(all_assumed_run, demo_model, settings)


def test_answer_rescores_without_ai(monkeypatch, demo_model, base, settings):
    monkeypatch.setattr("framework.agent._call", lambda *a, **k: pytest.fail("AI called"))
    before = base.model_dump()
    out = answer_and_rescore(base, demo_model, "steps.A11.dials.stakes", "5", settings)
    a = out.steps["A11"].dials["stakes"]
    assert a.value == 5 and a.basis == "answered" and a.confidence == "H"
    assert out.steps["A11"].tier.tier == "Human"
    assert "steps.A11.dials.stakes" not in [f.path for f in out.follow_ups]
    assert a.history and a.history[-1]["basis"] == "assumed"
    assert base.model_dump() == before


def test_rejects_invalid_value(demo_model, base, settings):
    before = base.model_dump()
    for path, bad in [("steps.A11.dials.stakes", "10"), ("steps.A11.r_pass1.R1", "yes please"),
                      ("context.volume", "  "), ("steps.A11.dials.stakes", True)]:
        with pytest.raises(ValueError):
            answer_and_rescore(base, demo_model, path, bad, settings)
    assert base.steps["A11"].dials["stakes"].basis == "assumed"
    assert base.model_dump() == before
    with pytest.raises(ValueError, match="isn't allowed here. Choose one of: 1, 2, 3, 4, 5"):
        answer_and_rescore(base, demo_model, "steps.A11.dials.stakes", "10", settings)


def test_coercion(demo_model, base, settings):
    out = answer_and_rescore(base, demo_model, "steps.A11.dials.stakes", "5.0", settings)
    assert out.steps["A11"].dials["stakes"].value == 5
    out = answer_and_rescore(base, demo_model, "steps.A11.r_pass1.R1", "unknown", settings)
    assert out.steps["A11"].r_pass1["R1"].value == "Unknown"
    out = answer_and_rescore(base, demo_model, "steps.A11.tier1.D1", "PARTLY", settings)
    assert out.steps["A11"].tier1["D1"].value == "Partly"
    out = answer_and_rescore(base, demo_model, "steps.A11.agent_test.A1", "y", settings)
    assert out.steps["A11"].agent_test["A1"].value == "Y"


def test_undo_answer(demo_model, base, settings):
    out = undo_answer(answer_and_rescore(base, demo_model, "context.volume", "40 leads a month", settings),
                      demo_model, "context.volume", settings)
    a = out.context["volume"]
    assert a.basis == "assumed" and a.history == []
    assert a.question == base.context["volume"].question


def test_two_answers_undo_one(demo_model, base, settings):
    r = answer_and_rescore(base, demo_model, "context.volume", "40", settings)
    r = answer_and_rescore(r, demo_model, "context.volume", "50", settings)
    r = undo_answer(r, demo_model, "context.volume", settings)
    assert r.context["volume"].value == "40" and r.context["volume"].basis == "answered"


def test_creates_missing_answers(demo_model, base, settings):
    base.steps["A2"].r_move = None
    out = answer_and_rescore(base, demo_model, "steps.A2.r_move", "remove", settings)
    assert out.steps["A2"].r_move.value == "Remove"
    out = answer_and_rescore(base, demo_model, "steps.A2.readiness.bus_factor", 2, settings)
    assert out.steps["A2"].readiness["bus_factor"].value == 2
    back = undo_answer(out, demo_model, "steps.A2.readiness.bus_factor", settings)
    assert "bus_factor" not in back.steps["A2"].readiness


def test_unknown_paths(demo_model, base, settings):
    for p in ["steps.ZZ.dials.stakes", "steps.A2.dials.nope", "bogus.x", "steps.A2.call"]:
        with pytest.raises(KeyError):
            answer_and_rescore(base, demo_model, p, "5", settings)
    with pytest.raises(ValueError):
        undo_answer(base, demo_model, "context.volume", settings)
