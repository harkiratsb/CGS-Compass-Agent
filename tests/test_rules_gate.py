import pytest

from framework.graph import graph_facts
from framework.models import Answer
from framework.rules import auto_gate_checks, score_gate


def test_auto_gate_checks_demo(demo_model):
    g = auto_gate_checks(demo_model, graph_facts(demo_model), names=["Liz", "Stephan"])
    assert set(g) == {"1", "2", "4", "5", "6", "7"}
    assert g["1"].value == "Y" and g["2"].value == "Y" and g["4"].value == "Y"
    assert g["6"].value in ("Partly", "N") and all(a.derived for a in g.values())
    assert all(a.basis == "stated" and a.reason for a in g.values())


def test_gate_role_with_name(demo_model):
    m = demo_model.model_copy(deep=True)
    m.roles[0].name = "Liz"
    assert auto_gate_checks(m, graph_facts(m), names=["Liz"])["2"].value == "N"


def test_gate_missing_end(demo_model):
    m = demo_model.model_copy(deep=True)
    m.activities = [a for a in m.activities if a.kind != "end"]
    assert auto_gate_checks(m, graph_facts(m), names=[])["1"].value == "N"


def test_gate_decision_without_question_mark(demo_model):
    m = demo_model.model_copy(deep=True)
    for d in m.decisions:
        d.question = d.question.rstrip("?")
    g = auto_gate_checks(m, graph_facts(m), names=[])
    assert g["4"].value == "N"


@pytest.mark.parametrize("ys,partlies,band", [(8, 0, "High"), (7, 0, "High"), (6, 1, "Medium"), (4, 0, "Medium"), (3, 1, "Low")])
def test_score_gate_bands(ys, partlies, band, settings):
    vals = ["Y"] * ys + ["Partly"] * partlies + ["N"] * (8 - ys - partlies)
    answers = {str(i + 1): Answer(value=v, basis="stated", derived=True) for i, v in enumerate(vals)}
    assert score_gate(answers, settings) == (ys + 0.5 * partlies, band)
