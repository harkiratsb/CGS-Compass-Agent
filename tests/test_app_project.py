from pathlib import Path

from streamlit.testing.v1 import AppTest

import schema
from project import save_project

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
JSON = "application/json"


def _fresh():
    return AppTest.from_file(APP, default_timeout=60).run()


def _project_text(demo_model):
    run = schema.AgentRun.model_validate_json((ROOT / "demo_data/agent_run_sales_process.json").read_text())
    return save_project([("saved.md", b"## 00:00:01\nhello")], {"process_id": "P-77"},
                        demo_model, run, "replay", True, None)


def _uploader(at):
    return at.sidebar.get("file_uploader")[0]


def test_open_project_restores_state(demo_model):
    at = _fresh()
    _uploader(at).set_value([("p.json", _project_text(demo_model).encode(), JSON)]).run()
    assert not at.exception
    assert at.session_state.model is not None
    assert [n for n, _ in at.session_state.files] == ["saved.md"]
    assert at.sidebar.text_input(key="process_id").value == "P-77"
    assert [t.value for t in at.toggle if t.key == "map_reviewed_toggle"] == [True]


def test_bad_project_shows_error_and_changes_nothing():
    at = _fresh()
    _uploader(at).set_value([("bad.json", b"nope", JSON)]).run()
    assert [e.value for e in at.error] == ["This file is not a valid project file."]
    assert at.session_state.model is None and at.session_state.files == []


def test_uploaded_material_does_not_clobber_opened_project(demo_model):
    at = _fresh()
    at.sidebar.get("file_uploader")[1].set_value([("up.md", b"upload", "text/markdown")]).run()
    assert [n for n, _ in at.session_state.files] == ["up.md"]
    _uploader(at).set_value([("p.json", _project_text(demo_model).encode(), JSON)]).run()
    assert [n for n, _ in at.session_state.files] == ["saved.md"]
    at.run()  # a later rerun still keeps the project's materials
    assert [n for n, _ in at.session_state.files] == ["saved.md"]
