import json
from pathlib import Path

import pytest

from framework.agent import apply_verifier, parse_answers, run_framework, stage1_context
from framework.graph import graph_facts
from framework.models import R_KEYS, YNU, Answer
from framework.rules import apply_rules
from ingest.agent import Truncated
from ingest.parse import Chunk, ParsedFile
from schema import AgentRun, SourceRef
from tests.fake_ai import fake_call_from

ROOT = Path(__file__).resolve().parent.parent
FAKE = "tests/fixtures/fake_ai_responses.json"


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr("framework.agent.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("framework.agent.DEMO_RUN", tmp_path / "no_demo_run.json")


def _cache_files():
    import framework.agent as fa
    return fa.CACHE_DIR.glob("framework_*.json") if fa.CACHE_DIR.exists() else []


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr("framework.agent._call", fake_call_from(FAKE))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


def test_parse_answers_tolerates_bad_ai_output():
    raw = {"R1": {"value": "Probably", "basis": "stated", "quotes": []}, "R2": {"value": "Y", "basis": "answered", "quotes": [{"file": "a", "location": "1", "quote": "q"}]}}
    out = parse_answers(raw, R_KEYS, YNU, {k: "Unknown" for k in R_KEYS}, {k: f"{k}?" for k in R_KEYS})
    assert out["R1"].basis == "assumed" and "invalid AI value" in out["R1"].flags
    assert out["R2"].basis == "assumed"            # AI can't claim "answered"
    assert out["R7"].value == "Unknown" and out["R7"].question == "R7?"


def test_parse_answers_keeps_quoted_values_and_defaults_unquoted_scores():
    raw = {"stakes": {"value": "4", "basis": "stated", "confidence": "H", "quotes": [{"file": "f", "location": "1", "quote": "q"}]},
           "judgment": {"value": 5, "basis": "stated", "quotes": []},
           "variation": "garbage"}
    keys = ["stakes", "judgment", "variation"]
    out = parse_answers(raw, keys, (1, 2, 3, 4, 5), {k: 3 for k in keys}, {k: k for k in keys})
    assert (out["stakes"].value, out["stakes"].basis, out["stakes"].confidence) == (4, "stated", "H")
    assert (out["judgment"].value, out["judgment"].basis) == (3, "assumed")   # no quote never raises a score
    assert out["variation"].value == 3 and out["variation"].basis == "assumed"


def test_apply_verifier_only_downgrades(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    run.steps["A6"].dials["stakes"] = Answer(value=4, basis="stated", confidence="H", quotes=[SourceRef(file="f", location="1", quote="q")])
    run.steps["A6"].dials["judgment"] = Answer(value=2, basis="answered")
    out = apply_verifier(run, [
        {"path": "steps.A6.dials.stakes", "action": "downgrade", "reason": "quote doesn't say"},
        {"path": "steps.A6.dials.judgment", "action": "downgrade", "reason": "x"},
        {"path": "steps.A6.dials.variation", "action": "raise", "reason": "x"},
        {"path": "steps.NOPE.dials.stakes", "action": "downgrade", "reason": "x"}])
    s = out.steps["A6"].dials
    assert (s["stakes"].basis, s["stakes"].value) == ("assumed", 3) and s["stakes"].history
    assert s["judgment"].value == 2


def test_apply_verifier_lowers_confidence_one_step(all_assumed_run):
    run = all_assumed_run.model_copy(deep=True)
    q = [SourceRef(file="f", location="1", quote="q")]
    run.steps["A2"].r_pass1["R1"] = Answer(value="Y", basis="stated", confidence="H", quotes=q)
    run.readiness_defaults["people"] = Answer(value=4, basis="stated", confidence="M", quotes=q)
    out = apply_verifier(run, [{"path": "steps.A2.r_pass1.R1", "action": "lower_confidence", "reason": "x"},
                               {"path": "readiness_defaults.people", "action": "lower_confidence", "reason": "x"}])
    assert out.steps["A2"].r_pass1["R1"].confidence == "M" and out.steps["A2"].r_pass1["R1"].value == "Y"
    assert out.readiness_defaults["people"].confidence == "L"
    assert run.steps["A2"].r_pass1["R1"].confidence == "H"      # input untouched


def test_verifier_lowering_survives_the_next_code_pass(all_assumed_run, demo_model, settings):
    run = all_assumed_run.model_copy(deep=True)
    run.steps["A6"].dials["stakes"] = Answer(
        value=4, basis="stated", confidence="M", quotes=[SourceRef(file="f", location="1", quote="q")],
        flags=["confidence capped: doc Medium (was H)"])
    out = apply_verifier(run, [{"path": "steps.A6.dials.stakes", "action": "lower_confidence", "reason": "weak"}])
    a = out.steps["A6"].dials["stakes"]
    assert a.confidence == "L" and not any(f.startswith("confidence capped:") for f in a.flags)
    assert apply_rules(out, demo_model, settings).steps["A6"].dials["stakes"].confidence == "L"


def test_run_framework_with_fake_ai(live, demo_model, settings):
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    assert [t.agent for t in run.trace][:4] == ["Orchestrator", "Documentation gate", "Toil Task Assessor", "Step Removal"]
    assert set(run.steps) == set(graph_facts(demo_model).steps)    # omitted steps still assessed (all assumed)
    assert run.follow_ups


def test_run_framework_end_to_end_values(live, demo_model, settings):
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    assert run.mode == "live" and run.map_reviewed and "ZZ9" not in run.steps
    assert run.doc_gate["3"].value == "Y"
    assert run.doc_gate["8"].value == "N"                         # an unquoted "Partly" takes the default
    assert run.gate_band == "Medium"
    assert run.context["outcome"].basis == "stated"
    assert [mc.type for mc in run.map_checks] == ["duplicate_gate"]
    a2 = run.steps["A2"]
    assert a2.worksheet["type_today"].basis == "stated" and a2.worksheet["type_today"].derived   # map prefill kept
    assert a2.worksheet["channel"].value is None and "Insightly web form" in a2.worksheet["channel"].reason
    assert "invalid AI value" in a2.r_pass1["R1"].flags
    assert a2.r_pass1["R3"].confidence == "L"                     # capped to M, then lowered by the verifier
    assert run.steps["D1"].call == "Remove"                       # R2 removes a gate check (code's call)
    assert run.steps["A7"].r_pass2["R4"].value == "N"
    assert run.steps["A7"].tier.tier == "Agent"
    assert run.steps["A3"].made_unnecessary_by == "A7"
    a6 = run.steps["A6"].dials["stakes"]
    assert (a6.basis, a6.value) == ("assumed", 3) and a6.history  # verifier downgrade
    assert run.readiness_defaults["governance"].value == 2
    assert run.readiness_defaults["people"].value == 1 and "invalid AI value" in run.readiness_defaults["people"].flags
    assert run.steps["A2"].readiness["data_systems"].value == 3
    assert [c.type for c in run.steps["A2"].recommendation.chain] == ["Rules", "Human task"]  # bad AI chain -> code default
    assert run.steps["A2"].specs == [{"type": "Rules", "owner": "Director of Operations"}]   # Agent spec dropped
    a7 = run.steps["A7"]
    assert [x.type for x in a7.recommendation.chain] == ["Agent", "Human approval", "Rules"]
    assert a7.recommendation.chain[2].role_id is None and len(a7.specs) == 3
    assert len(run.first_actions) == 3 and run.summary_text
    labels = [t.agent for t in run.trace]
    for label in ("Process vs Workflow Disambiguation", "Readiness", "Verifier (independent)",
                  "Workflow Documentation & Specification", "Summary"):
        assert label in labels
    assert labels.count("Step Removal") == 3
    for t in run.trace:
        if t.artifact:
            assert t.artifact in run.artifacts


def test_run_framework_caches_and_reloads(live, demo_model, settings, monkeypatch):
    first = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    monkeypatch.setattr("framework.agent._call", lambda *a, **k: pytest.fail("cache should be used"))
    seen = []
    again = run_framework(demo_model, [], map_reviewed=False, s=settings, progress=seen.append)
    assert again.steps == first.steps and not again.map_reviewed
    assert len(seen) == len(first.trace)


def test_replay_used_when_hash_matches(live, demo_model, settings, monkeypatch, tmp_path):
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    demo = tmp_path / "demo_run.json"
    demo.write_text(run.model_dump_json())
    monkeypatch.setattr("framework.agent.DEMO_RUN", demo)
    monkeypatch.setattr("framework.agent.CACHE_DIR", tmp_path / "empty_cache")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    replay = run_framework(demo_model, [], map_reviewed=True, s=settings)
    assert replay.mode == "replay" and replay.steps == run.steps


def test_truncated_step_splits_by_lane(monkeypatch, demo_model, settings):
    fake = fake_call_from(FAKE)
    part2_calls = []

    def call(system, user, **k):
        if user.startswith("[STEP:part2]"):
            part2_calls.append(user)
            if len(part2_calls) == 1:
                raise Truncated("too long")
        return fake(system, user, **k)
    monkeypatch.setattr("framework.agent._call", call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    lanes = {graph_facts(demo_model).actor[sid] for sid in graph_facts(demo_model).steps}
    assert len(part2_calls) == 1 + len(lanes)
    assert run.steps["D1"].call == "Remove"


def test_failed_ai_step_does_not_stop_the_run(monkeypatch, demo_model, settings):
    fake = fake_call_from(FAKE)

    def call(system, user, **k):
        if user.startswith("[STEP:tiers]"):
            return "not json"
        return fake(system, user, **k)
    monkeypatch.setattr("framework.agent._call", call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    step = next(t for t in run.trace if t.agent == "Process vs Workflow Disambiguation")
    assert step.status == "warning"
    assert run.steps["A2"].dials["rule_clarity"].basis == "assumed"
    assert not list(_cache_files())   # a failed run is never cached


def test_stage1_context_includes_pain_points_and_run(demo_model):
    run = AgentRun.model_validate_json((ROOT / "demo_data/agent_run_sales_process.json").read_text())
    ctx = stage1_context(demo_model, run)
    for heading in ("PAIN POINTS", "OPEN QUESTIONS", "SOURCE DISAGREEMENTS", "STEP DETAIL"):
        assert heading in ctx
    p = demo_model.pain_points[0]
    assert p.id in ctx and p.description[:30] in ctx
    assert "SOURCE DISAGREEMENTS" not in stage1_context(demo_model, None)


def test_stage1_context_details(demo_model):
    run = AgentRun.model_validate_json((ROOT / "demo_data/agent_run_sales_process.json").read_text())
    ctx = stage1_context(demo_model, run)
    heads = ["PAIN POINTS", "OPEN QUESTIONS", "SOURCE DISAGREEMENTS", "REJECTED VERIFIER FINDINGS",
             "HANDOFFS", "DEPENDENCIES", "STEP DETAIL"]
    assert [ctx.index(h) for h in heads] == sorted(ctx.index(h) for h in heads)
    assert "R3" in ctx and "Insightly" in ctx                    # manual -> R3 hint; tool names
    rejected = next(f for f in run.findings if not f.applied)
    assert rejected.id in ctx
    m = demo_model.model_copy(deep=True)
    m.open_questions = []
    assert "OPEN QUESTIONS" not in stage1_context(m, None)


def test_every_ai_prompt_gets_stage1_context(monkeypatch, demo_model, settings):
    prompts = []
    fake = fake_call_from(FAKE)
    monkeypatch.setattr("framework.agent._call", lambda system, user, **k: prompts.append(user) or fake(system, user, **k))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    judged = [u for u in prompts if not u.startswith(("[STEP:verify]", "[STEP:summary]"))]
    assert judged and all("PAIN POINTS" in u for u in judged)


def test_prompts_carry_sources_and_ground_rules(monkeypatch, demo_model, settings):
    calls = []
    fake = fake_call_from(FAKE)
    monkeypatch.setattr("framework.agent._call", lambda system, user, **k: calls.append((system, user, k)) or fake(system, user, **k))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    pf = ParsedFile(name="t.md", kind="transcript", chunks=[Chunk(file="t.md", location="00:01:00", text="UNIQUE-CHUNK")])
    run_framework(demo_model, [pf], map_reviewed=True, force_live=True, s=settings)
    for system, user, k in calls:
        assert "never decide calls, tiers or scores" in system.lower()
    judged = [u for _, u, _ in calls if not u.startswith("[STEP:summary]")]
    assert all("UNIQUE-CHUNK" in u for u in judged)
    from ingest.agent import REVIEW_MODEL
    assert [k.get("model") for _, u, k in calls if u.startswith("[STEP:verify]")] == [REVIEW_MODEL]


def test_run_framework_without_key_or_cache(monkeypatch, demo_model, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("framework.agent.CACHE_DIR", tmp_path)
    m = demo_model.model_copy(deep=True); m.process.name = "Something else"
    with pytest.raises(RuntimeError, match="No API key"):
        run_framework(m, [], map_reviewed=False)


def test_rescore_keeps_a_valid_ai_chain_and_drops_an_incompatible_one(live, demo_model, settings):
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    again = apply_rules(run, demo_model, settings)
    assert [x.name for x in again.steps["A7"].recommendation.chain][0] == "Draft outreach email"
    again.steps["A7"].dials["stakes"] = Answer(value=5, basis="answered")      # tier becomes Human
    human = apply_rules(again, demo_model, settings).steps["A7"]
    assert human.tier.tier == "Human"
    assert [x.type for x in human.recommendation.chain] == ["Human task"]


def test_cache_hit_rescoring_honours_success_end_and_map_reviewed(live, demo_model, settings, monkeypatch):
    first = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    other_end = next(e for e in graph_facts(demo_model).end_ids if e != first.success_end)
    monkeypatch.setattr("framework.agent._call", lambda *a, **k: pytest.fail("cache should be used"))
    again = run_framework(demo_model, [], map_reviewed=False, success_end=other_end, s=settings)
    assert again.success_end == other_end and not again.map_reviewed
    assert [x.type for x in again.steps["A7"].recommendation.chain] == ["Agent", "Human approval", "Rules"]


def test_corrupt_cache_file_is_a_miss(live, demo_model, settings):
    import framework.agent as fa
    fa.CACHE_DIR.mkdir(parents=True)
    (fa.CACHE_DIR / f"framework_{fa.framework_hash(demo_model, [], settings)}.json").write_text("{not json")
    fa.DEMO_RUN.write_text('{"mode": "nonsense"}')
    run = run_framework(demo_model, [], map_reviewed=True, s=settings)
    assert run.mode == "live" and run.trace


def test_unquoted_free_text_is_not_treated_as_fact(monkeypatch, demo_model, settings):
    prompts = []
    fake = fake_call_from(FAKE)
    monkeypatch.setattr("framework.agent._call", lambda system, user, **k: prompts.append(user) or fake(system, user, **k))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    run = run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings)
    vol = run.context["volume"]
    assert vol.value is None and vol.basis == "assumed" and "About 10 leads a month" in vol.reason
    later = [u for u in prompts if u.startswith(("[STEP:part2_context]", "[STEP:summary]"))]
    assert len(later) == 2 and not any("About 10 leads" in u for u in later)
    assert all("signed engagement" in u for u in later)                    # the stated outcome does go in


def test_first_actions_must_be_a_list(monkeypatch, demo_model, settings):
    fake = fake_call_from(FAKE)

    def call(system, user, **k):
        if user.startswith("[STEP:summary]"):
            return json.dumps({"summary_text": "x", "first_actions": "Do everything"})
        return fake(system, user, **k)
    monkeypatch.setattr("framework.agent._call", call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert run_framework(demo_model, [], map_reviewed=True, force_live=True, s=settings).first_actions == []


def test_framework_hash_covers_names_locations_and_settings(demo_model, settings):
    from framework.agent import framework_hash

    def pf(name, loc, text="t"):
        return [ParsedFile(name=name, kind="transcript", chunks=[Chunk(file=name, location=loc, text=text)])]
    base = framework_hash(demo_model, pf("a.md", "1"), settings)
    assert base != framework_hash(demo_model, pf("b.md", "1"), settings)
    assert base != framework_hash(demo_model, pf("a.md", "2"), settings)
    assert framework_hash(demo_model, pf("a", "b"), settings) != framework_hash(demo_model, pf("ab", ""), settings)
    changed = settings.model_copy(deep=True)
    changed.defaults.dial = 2
    assert base != framework_hash(demo_model, pf("a.md", "1"), changed)
