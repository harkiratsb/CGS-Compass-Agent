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
