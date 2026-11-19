"""Phase 3 screens end to end in Streamlit AppTest: demo → analyze → reviewed → run → answer → future state."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import schema
from framework.models import FrameworkRun
from project import save_project
from tests.fake_ai import fake_call_from

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
PHASE3 = "Phase 3 · Future state"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr("framework.agent.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("framework.agent.DEMO_RUN", tmp_path / "no_demo_run.json")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr("framework.agent._call", fake_call_from("tests/fixtures/fake_ai_responses.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


def _analyzed():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.button[0].click().run()                       # Load demo materials
    next(b for b in at.sidebar.button if "Analyze" in b.label).click().run()
    assert not at.exception and at.session_state.model is not None
    return at


def _phase3(at):
    at.segmented_control(key="view").set_value(PHASE3).run()
    assert not at.exception
    return at


def _errors(at):
    return [e.value for e in at.error]


def _run_button(at):
    return at.button(key="p3_run")


def test_phase3_flow_with_a_run(live):
    at = _analyzed()
    at.toggle(key="map_reviewed_toggle").set_value(True).run()
    _phase3(at)
    assert any("Run the framework first" in i.value for i in at.info)
    _run_button(at).click().run()
    assert not at.exception and not _errors(at), _errors(at)
    run = at.session_state.framework
    assert isinstance(run, FrameworkRun) and run.map_reviewed and run.mode == "live"
    trace_len = len(run.trace)

    # answer a follow-up (volume) through the form; rescoring adds no trace entries
    path = "context.volume"
    assert path in [f.path for f in run.follow_ups]
    box = next(t for t in at.text_input if t.key.startswith(f"p3_fu_{path}_"))
    box.input("40 per month")
    next(b for b in at.button if b.label == "Save answers").click().run()
    assert not at.exception and not _errors(at), _errors(at)
    after = at.session_state.framework
    assert after.context["volume"].basis == "answered" and after.context["volume"].value == "40 per month"
    assert path not in [f.path for f in after.follow_ups]
    assert len(after.trace) == trace_len
    assert any("Saved 1 answer" in s.value for s in at.success)

    # override a step answer on the Steps screen
    sid = at.selectbox(key="p3_step").value
    stakes = next(s for s in at.selectbox if s.key and s.key.startswith(f"p3_w_steps.{sid}.dials.stakes_"))
    stakes.set_value(5 if stakes.value != 5 else 4)
    form_save = [b for b in at.button if b.label == "Save"]
    # the dials form is the one holding the stakes widget; click every Save is harmless (unchanged forms do nothing)
    for b in form_save:
        if b.form_id and "dials" in b.form_id:
            b.click().run()
            break
    assert not at.exception and not _errors(at), _errors(at)
    assert at.session_state.framework.steps[sid].dials["stakes"].basis == "answered"

    # future state renders (the fake run's expanded map needs more than 24 lettered connectors)
    at.toggle(key="p3_show_removed").set_value(True).run()
    assert not at.exception and not _errors(at), _errors(at)
    assert any("Outcome and goal" in m.value for m in at.markdown)


def test_no_key_shows_error_and_other_screens_render(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    at = _phase3(_analyzed())
    _run_button(at).click().run()
    assert not at.exception
    assert any("No API key" in e.value for e in at.error)
    assert at.session_state.framework is None
    assert sum("Run the framework first" in i.value for i in at.info) == 4   # the other four screens
    assert any("isn't marked as reviewed" in w.value for w in at.warning)


def test_opened_project_without_run_and_with_framework(demo_model, acceptance_run):
    text = save_project([("saved.md", b"## 00:00:01\nhello")], {}, demo_model, None, None, True,
                        acceptance_run.model_dump())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.get("file_uploader")[0].set_value([("p.json", text.encode(), "application/json")]).run()
    assert not at.exception
    assert any("No Stage 1 agent run" in i.value for i in at.info)
    assert isinstance(at.session_state.framework, FrameworkRun)
    _phase3(at)
    assert not at.error            # including the diagram: the acceptance map draws
    assert any("Outcome and goal" in m.value for m in at.markdown)
    # the reviewed toggle survives a trip to Phase 3 and back
    at.segmented_control(key="view").set_value("Stage 1 · Current state").run()
    assert [t.value for t in at.toggle if t.key == "map_reviewed_toggle"] == [True]


def test_names_kept_out_of_phase3_screens(demo_model, acceptance_run):
    run = acceptance_run.model_copy(deep=True)
    run.context["outcome"].value = "Jordyn closes the deal"
    run.context["outcome"].reason = "Jordyn said so"
    proj = save_project([("t.md", b"**Jordyn Smith:** hello\n")], {}, demo_model,
                        schema.AgentRun.model_validate_json((ROOT / "demo_data/agent_run_sales_process.json").read_text()),
                        "replay", True, run.model_dump())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.get("file_uploader")[0].set_value([("p.json", proj.encode(), "application/json")]).run()
    _phase3(at)
    shown = " ".join(m.value for m in at.markdown)
    assert "Outcome and goal" in shown and "Jordyn" not in shown


def test_success_end_placeholder_and_stale_warning(demo_model, acceptance_run):
    run = acceptance_run.model_copy(deep=True)
    run.success_end = None
    run.artifacts["input_hash"] = "not-this-map"
    text = save_project([("saved.md", b"## 00:00:01\nhello")], {}, demo_model, None, None, True, run.model_dump())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.get("file_uploader")[0].set_value([("p.json", text.encode(), "application/json")]).run()
    _phase3(at)
    assert not at.error
    sel = next(s for s in at.selectbox if s.label.startswith("Success end"))
    assert sel.value is None                                   # placeholder, nothing applied
    assert at.session_state.framework.success_end is None
    assert sum("changed after this framework run" in w.value for w in at.warning) == 1
    sel.set_value(sel.options[1]).run()
    assert at.session_state.framework.success_end == sel.options[1].split(" · ")[0]


def test_phase3_syncs_map_reviewed_from_the_toggle(demo_model, acceptance_run):
    run = acceptance_run.model_copy(deep=True)
    run.map_reviewed = True
    text = save_project([("saved.md", b"## 00:00:01\nhello")], {}, demo_model, None, None, False, run.model_dump())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.get("file_uploader")[0].set_value([("p.json", text.encode(), "application/json")]).run()
    _phase3(at)
    assert at.session_state.framework.map_reviewed is False
    assert any("not marked as reviewed before this run" in m.value for m in at.markdown)
