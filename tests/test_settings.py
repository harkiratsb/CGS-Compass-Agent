import pytest

from framework.settings import load_settings
from tests.conftest import ROOT


def test_load_settings_defaults():
    s = load_settings()
    assert s.gate.high == 7.0 and s.gate.medium == 4.0
    assert s.redesign.bands == [10, 25, 40, 60]
    assert s.defaults.dial == 3 and s.defaults.rule_clarity == 1 and s.defaults.readiness == 1
    assert s.tier.agent.approval_stakes_min == 3 and s.tier.human.stakes == 5
    assert s.readiness.ready_min == 4 and s.loop.extra_passes == 1


def test_load_settings_custom_file(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text((ROOT / "framework/settings.toml").read_text().replace("high = 7.0", "high = 6.5"))
    assert load_settings(p).gate.high == 6.5


def test_load_settings_rejects_missing_key(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text("[gate]\nhigh = 7.0\n")
    with pytest.raises(ValueError):
        load_settings(p)


def test_demo_model_fixture(demo_model):
    assert len(demo_model.decisions) == 8 and demo_model.validate_graph() == []


def _write(tmp_path, *swaps):
    text = (ROOT / "framework/settings.toml").read_text()
    for old, new in swaps:
        assert old in text
        text = text.replace(old, new)
    p = tmp_path / "s.toml"
    p.write_text(text)
    return p


@pytest.mark.parametrize("swap", [
    ("bands = [10, 25, 40, 60]", "bands = [10, 25, 40]"),
    ("bands = [10, 25, 40, 60]", "bands = [10, 25, 40, 60, 80]"),
    ("bands = [10, 25, 40, 60]", "bands = [10, 40, 25, 60]"),
    ("bands = [10, 25, 40, 60]", "bands = [10, 25, 25, 60]"),
    ("high = 7.0", "high = 4.0"),
    ("high = 7.0", "high = 3.0"),
    ("dial = 3", "dial = 0"),
    ("dial = 3", "dial = 6"),
    ("rule_clarity = 1", "rule_clarity = 7"),
    ("readiness = 1", "readiness = 0"),
    ("clarity_min = 4", "clarity_min = 6"),
    ("stakes = 5", "stakes = 0"),
    ("checkability_max = 2", "checkability_max = 9"),
])
def test_load_settings_rejects_bad_values(tmp_path, swap):
    with pytest.raises(ValueError):
        load_settings(_write(tmp_path, swap))


def test_load_settings_missing_file_is_plain_error(tmp_path):
    with pytest.raises(ValueError, match="nope.toml"):
        load_settings(tmp_path / "nope.toml")


def test_load_settings_bad_toml_is_plain_error(tmp_path):
    p = tmp_path / "broken.toml"
    p.write_text("[gate\nhigh = ")
    with pytest.raises(ValueError, match="broken.toml"):
        load_settings(p)
