import pytest

from framework.models import R_KEYS, Answer, StepAssessment
from framework.rules import part2_call, redesign_potential, yes


def _a(v):
    return Answer(value=v, basis="stated", derived=True)


def _step(sid="A5", **r):
    return StepAssessment(
        step_id=sid, worksheet={},
        r_pass1={k: _a(r.get(k, "N")) for k in R_KEYS},
    )


def test_yes_only_for_y():
    assert yes(_a("Y"))
    assert not yes(_a("Unknown")) and not yes(_a("N")) and not yes(None)


def test_first_match_wins(demo_facts):
    assert part2_call(_step(R2="Y", R7="Y"), demo_facts, False)[0] == "Remove"


def test_unknown_counts_as_no(demo_facts):
    assert part2_call(_step(R1="Unknown"), demo_facts, False)[0] == "Keep"


def test_decision_protected_except_r2(demo_facts):
    s = _step("D3", R1="Y")
    assert part2_call(s, demo_facts, True)[0] == "Keep"
    assert s.protected
    s2 = _step("D1", R2="Y")
    assert part2_call(s2, demo_facts, True)[0] == "Remove"


def test_accountability_protects(demo_facts):
    s = _step(R1="Y")
    s.accountability = _a("Y")
    assert part2_call(s, demo_facts, False)[0] == "Keep"
    assert s.protected


def test_unprotected_step_not_flagged(demo_facts):
    s = _step(R1="Y")
    assert part2_call(s, demo_facts, False)[0] == "Remove"
    assert not s.protected


def test_r3_combine_target(demo_facts):
    s = _step(R3="Y")
    s.combine_with = _a("A6")
    assert part2_call(s, demo_facts, False) == ("Combine", "A6")
    s.combine_with = _a("somewhere vague")
    assert part2_call(s, demo_facts, False) == ("Combine", None)


def test_r4_move_or_remove(demo_facts):
    s = _step(R4="Y")
    s.r_move = _a("Move")
    assert part2_call(s, demo_facts, False)[0] == "Move"
    s.r_move = _a("Remove")
    assert part2_call(s, demo_facts, False)[0] == "Remove"


def test_r6_needs_same_actor_neighbour(demo_facts):
    s = _step("A1", R6="Y")
    s.combine_with = _a("A24")  # not adjacent
    assert part2_call(s, demo_facts, False)[0] == "Keep"


def test_r6_combines_with_neighbour(demo_facts):
    sid, nbrs = next((k, v) for k, v in demo_facts.same_actor_neighbours.items() if v)
    other = sorted(nbrs)[0]
    s = _step(sid, R6="Y")
    s.combine_with = _a(other)
    assert part2_call(s, demo_facts, False) == ("Combine", other)


def test_r7_fix_the_map(demo_facts):
    assert part2_call(_step(R7="Y"), demo_facts, False)[0] == "Fix the map"


def test_pass2_overrides_pass1(demo_facts):
    s = _step(R1="Y")
    s.r_pass2 = {"R1": _a("N")}
    assert part2_call(s, demo_facts, False)[0] == "Keep"


@pytest.mark.parametrize("non_keep,total,score", [
    (0, 10, 1), (1, 10, 2), (3, 10, 3), (4, 10, 4), (6, 10, 5), (0, 0, 1)])
def test_redesign_potential(non_keep, total, score, settings):
    calls = ["Remove"] * non_keep + ["Keep"] * (total - non_keep)
    assert redesign_potential(calls, settings) == score
