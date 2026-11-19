import pytest

from framework.impact import GAP_VOLUME, impact, parse_volume
from framework.agent import DEMO_RUN
from framework.demo import demo_state
from framework.models import Answer, FrameworkRun
from framework.rules import apply_rules
from framework.outputs import summary_markdown


@pytest.fixture
def replay(settings):
    _, _, _, model = demo_state()
    return apply_rules(FrameworkRun.model_validate_json(DEMO_RUN.read_text()), model, settings), model


def _vol(run, value, basis="answered"):
    run.context["volume"] = Answer(value=value, basis=basis, derived=True, confidence="H")
    return run


@pytest.mark.parametrize("text,expected", [
    ("40 leads a month", 40), ("40/month", 40), ("40 per month", 40), ("10 per week", 10 * 4.33),
    ("500 a year", 500 / 12), ("1,200 a year", 100), ("12 per quarter", 4), ("2.5 a week", 2.5 * 4.33),
])
def test_parse_volume(text, expected):
    assert parse_volume(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "a lot", "about 40", "40", "some per month", "5 a day", None, 40])
def test_parse_volume_unparseable(text):
    assert parse_volume(text) is None


def test_demo_figures_are_consistent(demo_model, acceptance_run):
    imp = impact(acceptance_run, demo_model)
    removed = [sid for sid, st in acceptance_run.steps.items() if st.call == "Remove"]
    acts = {a.id: a for a in demo_model.activities} | {d.id: d for d in demo_model.decisions}
    want = sum(acts[s].human_minutes_per_cycle or 0 for s in removed)
    assert imp.removed_minutes == pytest.approx(want) and want > 0
    assert {r.step_id for r in imp.removed_steps} == set(removed)
    assert imp.always_minutes + imp.conditional_minutes == pytest.approx(imp.removed_minutes)
    assert imp.stated_minutes + imp.estimated_minutes == pytest.approx(imp.total_minutes)
    assert imp.steps_without_minutes == sum(1 for s in acceptance_run.steps if acts[s].human_minutes_per_cycle is None)
    assert imp.decisions_after < imp.decisions_before
    assert imp.pains_resolved + imp.pains_open == imp.pains_total


def test_director_of_operations_is_freed_in_the_shipped_replay(replay):
    run, model = replay
    imp = impact(run, model)
    freed = {r.name: r for r in imp.freed_roles}
    assert freed["Director of Operations"].steps_before == 4
    assert freed["Director of Operations"].removed_minutes > 0
    assert imp.removed_minutes == sum(
        a.human_minutes_per_cycle or 0 for a in [*model.activities, *model.decisions]
        if run.steps.get(a.id) and run.steps[a.id].call == "Remove")
    md = summary_markdown(run, model)
    assert "Director of Operations" in md.split("## Expected impact")[1].split("\n## ")[0]


def test_no_volume_gives_gap_and_no_monthly(demo_model, acceptance_run):
    imp = impact(acceptance_run, demo_model)
    assert imp.leads_per_month is None and imp.hours_per_month is None
    assert imp.volume_gap == GAP_VOLUME == "Gap: answer the volume question to see hours saved per month"


def test_assumed_volume_gives_no_monthly(demo_model, acceptance_run):
    imp = impact(_vol(acceptance_run, "40 leads a month", "assumed"), demo_model)
    assert imp.hours_per_month is None and imp.volume_gap == GAP_VOLUME


def test_unparseable_volume_gives_gap(demo_model, acceptance_run):
    imp = impact(_vol(acceptance_run, "quite a few"), demo_model)
    assert imp.hours_per_month is None and imp.volume_gap == GAP_VOLUME


def test_monthly_hours(demo_model, acceptance_run):
    imp = impact(_vol(acceptance_run, "40 leads a month"), demo_model)
    assert imp.leads_per_month == 40 and imp.volume_gap == ""
    assert imp.hours_per_month == pytest.approx(imp.removed_minutes * 40 / 60)
    assert imp.hours_per_month_always == pytest.approx(imp.always_minutes * 40 / 60)


def test_conditional_versus_always_minutes(demo_model, acceptance_run):
    imp = impact(acceptance_run, demo_model)
    by = {r.step_id: r for r in imp.removed_steps}
    # A3 and A4 sit behind the "has Insightly access" decision
    assert by["A3"].conditional and by["A4"].conditional
    assert imp.conditional_minutes == pytest.approx(sum(r.minutes for r in by.values() if r.conditional))
    assert imp.conditional_minutes > 0


def test_combined_steps_listed_not_removed(demo_model, acceptance_run):
    run = acceptance_run
    run.steps["A9"].call, run.steps["A9"].combine_target = "Combine", "A10"
    imp = impact(run, demo_model)
    assert [m.step_id for m in imp.merged_steps] == ["A9"]
    assert "A9" not in {r.step_id for r in imp.removed_steps}
    assert imp.merged_minutes == 10


def test_summary_section_order_and_content(demo_model, acceptance_run):
    md = summary_markdown(acceptance_run, demo_model)
    heads = [l[3:] for l in md.splitlines() if l.startswith("## ")]
    assert heads.index("Expected impact") == heads.index("Redesign potential") + 1
    assert GAP_VOLUME in md
    md = summary_markdown(_vol(acceptance_run, "40 leads a month"), demo_model)
    assert "hours a month" in md and GAP_VOLUME not in md


def test_future_screen_shows_three_metrics(demo_model, acceptance_run):
    from tests.test_app_phase3 import APP, _phase3
    from project import save_project
    from streamlit.testing.v1 import AppTest

    acceptance_run.context["volume"] = Answer(value="40 leads a month", basis="answered", derived=True)
    text = save_project([("saved.md", b"## 00:00:01\nhello")], {}, demo_model, None, None, True,
                        acceptance_run.model_dump())
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.get("file_uploader")[0].set_value([("p.json", text.encode(), "application/json")]).run()
    _phase3(at)
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert {"Minutes saved per lead", "Hours saved per month", "Roles freed"} <= set(labels)
    assert labels["Hours saved per month"] != "Needs volume"
