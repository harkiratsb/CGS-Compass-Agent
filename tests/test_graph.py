from framework.graph import graph_facts


def test_graph_facts_demo(demo_model):
    f = graph_facts(demo_model)
    assert "S0" not in f.steps and "E1" not in f.steps and "D1" in f.steps
    assert f.success_end in f.end_ids
    assert "A1" in f.main_path
    assert f.actor["A2"] == next(a.role_id for a in demo_model.activities if a.id == "A2")


def test_success_end_override(demo_model):
    assert graph_facts(demo_model, success_end="E1").success_end == "E1"


def test_graph_facts_edge_cases(demo_model):
    m = demo_model.model_copy(deep=True)
    m.edges = []
    for d in m.decisions:
        d.branches = []
    f = graph_facts(m)
    assert f.success_end is None and f.main_path == set()
    one_lane = demo_model.model_copy(deep=True)
    for a in one_lane.activities:
        a.role_id = one_lane.roles[0].id
    for d in one_lane.decisions:
        d.role_id = one_lane.roles[0].id
    g = graph_facts(one_lane)
    assert g.same_actor_neighbours["A1"]
    assert all(k in g.same_actor_neighbours for k in g.steps)
