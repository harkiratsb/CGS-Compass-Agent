"""Acceptance test on CGS's worked example (docs/cgs-framework-starter.md)."""
from framework.future import build_future_model


def test_cgs_example_verdicts(acceptance_run):
    st = acceptance_run.steps
    assert st["D1"].call == st["D5"].call == st["D8"].call == "Remove"
    assert st["A2"].call == "Keep" and st["A2"].tier.standardize_first and st["A2"].tier.tier == "Rules"
    assert st["A6"].call == "Keep" and st["A6"].tier.tier == "Agent" and st["A6"].tier.approval
    assert st["A11"].call == "Keep" and st["A11"].tier.tier == "Human"
    assert all(st[s].call in ("Remove", "Combine") for s in ("A3", "A4"))


def test_cgs_aggregate_reported(acceptance_run, capsys):
    kept = [s for s in acceptance_run.steps.values() if s.call == "Keep" and s.tier]
    share = sum(s.tier.tier in ("Rules", "Human") for s in kept)
    print(f"{share} of {len(kept)} kept steps end at Rules or Human (CGS: 17 of 25 on their 28-step map)")


def test_acceptance_future_graph_valid(demo_model, acceptance_run):
    assert build_future_model(demo_model, acceptance_run)[0].validate_graph() == []
