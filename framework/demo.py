"""The demo state, built the way the app builds it (no side effects on import).

demo materials -> shipped Stage 1 replay -> dedupe -> process name and owner from the app's
intake defaults. The framework replay only matches in the app when this map is unchanged.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def demo_state():
    """(files, parsed, agent_run, model) for the demo materials."""
    from ingest.agent import DEMO_RUN as STAGE1_REPLAY
    from ingest.merge import dedupe_activities
    from ingest.parse import parse_file
    from intake import INTAKE_DEFAULTS
    from schema import AgentRun

    files = [(p.name, p.read_bytes()) for p in sorted((ROOT / "demo_data").glob("*.md"))]
    parsed = [parse_file(n, b) for n, b in files]
    # the shipped Stage 1 replay (what a fresh copy of the app uses), not a local cache, so the replay travels
    agent_run = AgentRun.model_validate_json(STAGE1_REPLAY.read_text())
    model = dedupe_activities(agent_run.final)
    model.process.name, model.process.owner = INTAKE_DEFAULTS["process_name"], INTAKE_DEFAULTS["process_owner"]
    return files, parsed, agent_run, model
