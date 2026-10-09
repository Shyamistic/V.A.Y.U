from monsoon_grid.cli import execute_replay


def test_same_seed_replay_has_same_deterministic_outputs():
    first = execute_replay(seed=20260701, policy_mode="balanced")
    second = execute_replay(seed=20260701, policy_mode="balanced")
    assert first.scenarios.realized_path_sha256 == second.scenarios.realized_path_sha256
    assert [state.model_dump_json() for state in first.twin.slot_states] == [state.model_dump_json() for state in second.twin.slot_states]
    assert first.twin.metrics.model_dump_json() == second.twin.metrics.model_dump_json()
