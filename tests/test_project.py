import pytest

from ingest.parse import parse_file
from project import PROJECT_VERSION, Project, ProjectError, load_project, parsed_files, save_project


def test_project_round_trip(demo_model):
    files = [("a.md", b"## 00:00:01\nhello")]
    text = save_project(files, {"process_id": "P-01"}, demo_model, None, "replay", True, None)
    proj, back = load_project(text)
    assert back == files and proj.map_reviewed is True
    assert proj.model.model_dump() == demo_model.model_dump()
    assert parsed_files(back)[0].chunks[0].location == "00:00:01"


def test_round_trip_keeps_intake_mode_framework_and_binary(demo_model):
    files = [("b.pdf", bytes(range(256)))]
    fw = {"anything": [1, 2]}
    text = save_project(files, {"process_id": "P-01", "extra_names": ""}, demo_model, None, "live", False, fw)
    proj, back = load_project(text)
    assert back == files
    assert proj.version == PROJECT_VERSION
    assert proj.intake == {"process_id": "P-01", "extra_names": ""}
    assert proj.mode == "live" and proj.map_reviewed is False and proj.framework == fw


def test_parsed_files_matches_direct_parse():
    files = [("a.md", b"## 00:00:01\nhello"), ("n.txt", b"plain notes")]
    assert [p.summary for p in parsed_files(files)] == [parse_file(n, b).summary for n, b in files]


@pytest.mark.parametrize("bad", ["not json", "{}", '{"version": 99, "files": [], "intake": {}, "model": null}'])
def test_load_project_rejects_bad_files(bad):
    with pytest.raises(ProjectError):
        load_project(bad)


def test_version_mismatch_message(demo_model):
    text = save_project([], {}, demo_model, None, None, False, None).replace('"version": 1', '"version": 7')
    with pytest.raises(ProjectError, match=r"different version of the app \(version 7\)"):
        load_project(text)


def test_bad_base64_is_project_error(demo_model):
    text = save_project([("a.md", b"x")], {}, demo_model, None, None, False, None)
    proj = Project.model_validate_json(text)
    proj.files[0].data_b64 = "!!notbase64!!"
    with pytest.raises(ProjectError):
        load_project(proj.model_dump_json())
