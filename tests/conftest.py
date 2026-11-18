from pathlib import Path

import pytest

import schema
from framework.settings import load_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def demo_model():
    run = schema.AgentRun.model_validate_json(
        (ROOT / "demo_data/agent_run_sales_process.json").read_text()
    )
    return run.final


@pytest.fixture
def minimal_run():
    from framework.models import DIAL_KEYS, FrameworkRun, StepAssessment, assumed

    step = StepAssessment(
        step_id="A2",
        worksheet={},
        r_pass1={},
        dials={k: assumed(3, f"Rate {k}") for k in DIAL_KEYS},
    )
    return FrameworkRun(
        mode="fixture", map_reviewed=False, doc_gate={}, context={},
        readiness_defaults={}, steps={"A2": step},
    )


@pytest.fixture
def demo_facts(demo_model):
    from framework.graph import graph_facts

    return graph_facts(demo_model)


@pytest.fixture
def all_assumed_run(demo_model):
    from framework.models import blank_run

    return blank_run(demo_model, "fixture")


@pytest.fixture
def acceptance_run(demo_model, settings):
    from framework.models import blank_run, load_answers
    from framework.rules import apply_rules

    run = load_answers(blank_run(demo_model, "fixture"), ROOT / "tests/fixtures/demo_answers.json")
    return apply_rules(run, demo_model, settings)
