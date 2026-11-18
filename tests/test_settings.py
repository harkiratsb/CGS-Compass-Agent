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
