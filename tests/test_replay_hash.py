"""The shipped demo replay is keyed on the map and sources only, so editing thresholds keeps it working."""
import json

from framework.agent import DEMO_RUN, framework_hash
from framework.demo import demo_state


def test_demo_state_hash_matches_shipped_replay(settings):
    _, parsed, _, model = demo_state()
    assert framework_hash(model, parsed, settings) == json.loads(DEMO_RUN.read_text())["artifacts"]["input_hash"]


def test_thresholds_do_not_change_the_hash(settings):
    _, parsed, _, model = demo_state()
    changed = settings.model_copy(deep=True)
    changed.tier.human.stakes = 4
    changed.gate.high = 6.0
    changed.defaults.dial = 2
    assert framework_hash(model, parsed, changed) == framework_hash(model, parsed, settings)
    more = settings.model_copy(deep=True)
    more.loop.extra_passes = 2          # a different AI pipeline does count
    assert framework_hash(model, parsed, more) != framework_hash(model, parsed, settings)
