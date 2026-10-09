from monsoon_grid.cli import execute_replay


def test_full_twin_has_one_slot_trace_and_unsafe_rejection():
    execution = execute_replay(seed=20260701, policy_mode="balanced")
    assert len(execution.twin.slot_states) == 96
    assert execution.scenarios.realized_path_id == "scenario-02"
    assert execution.decisions[41].status in {"REJECTED", "ESCALATED_NO_SAFE_ACTION"}
    assert not execution.twin.slot_states[41].action_applied
    assert execution.twin.metrics.trace_complete
