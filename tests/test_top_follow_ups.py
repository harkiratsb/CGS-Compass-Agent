"""Only the top 10 open questions are shown: the ones whose answer could change a result."""
import time

import pytest

from framework import followups
from framework.agent import run_framework
from framework.demo import demo_state
from framework.followups import ensure_top_questions, top_follow_ups
from framework.models import GATE_KEYS, Answer
from framework.rules import answer_and_rescore, apply_rules
from handoff import speaker_names


@pytest.fixture(scope="module")
def demo():
    from framework.settings import load_settings
    files, parsed, agent_run, model = demo_state()
    s, names = load_settings(), speaker_names(files)
    return run_framework(model, parsed, agent_run=agent_run, map_reviewed=True, s=s, names=names), model, s, names


def test_demo_top_ten_change_results_and_run_fast(demo):
    run, model, s, names = demo
    t0 = time.time()
    top = top_follow_ups(run, model, s, names)
    assert time.time() - t0 < 2.5
    assert 0 < len(top) <= 10 and len(run.follow_ups) > 10
    assert all(q.effect.startswith("Could") for q in top)
    assert all(".worksheet." not in p for q in top for p in q.paths)
    assert top[0].effect.count("call") == 1                 # a call change ranks first


def _high(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    for k in GATE_KEYS:
        run.doc_gate[k] = Answer(value="Y", basis="answered")
    for st in run.steps.values():                         # rule out every R question
        for k, a in st.r_pass1.items():
            st.r_pass1[k] = Answer(value="N", basis="answered")
    return run


def test_groups_and_order(demo_model, all_assumed_run, settings):
    run = apply_rules(_high(all_assumed_run), demo_model, settings)
    top = top_follow_ups(run, demo_model, settings, n=200)
    qs = [q.question for q in top]
    assert not any("worksheet" in p for q in top for p in q.paths)
    d = next(q for q in top if q.paths[0].endswith("tier1.D1"))
    assert d.question.startswith("Is ") and d.question.endswith("done digitally, a written standard way, with findable fields?")
    assert len(d.paths) == 3
    assert any(len(q.paths) > 1 and ".agent_test." in q.paths[0] for q in top)
    ctx = [i for i, q in enumerate(top) if q.paths[0].startswith("context.")]
    assert ctx and ctx == list(range(len(top) - len(ctx), len(top)))      # context questions last
    assert all(top[i].question.endswith("(used on the next AI run)") for i in ctx)
    assert {top[i].paths[0] for i in ctx} <= {"context.goal", "context.baselines", "context.volume"}
    ready = [i for i, q in enumerate(top) if q.paths[0].startswith("readiness_defaults.")]
    assert ready and ready[0] < ctx[0]
    assert len(top_follow_ups(run, demo_model, settings)) == 10


def test_cached_until_the_run_changes(monkeypatch, demo_model, all_assumed_run, settings):
    run = apply_rules(all_assumed_run, demo_model, settings)
    calls = []
    real = followups.top_follow_ups
    monkeypatch.setattr(followups, "top_follow_ups", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = ensure_top_questions(run, demo_model, settings)
    assert ensure_top_questions(run, demo_model, settings) == first and len(calls) == 1
    after = answer_and_rescore(run, demo_model, "context.volume", "40", settings)
    ensure_top_questions(after, demo_model, settings)
    assert len(calls) == 2
    assert len(run.follow_ups) > len(first)                # run.follow_ups stays complete
