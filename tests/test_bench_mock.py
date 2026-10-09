from monsoon_grid.bench_mock import MockBench


def test_mock_bench_safe_state_and_recovery():
    bench = MockBench.create()
    assert bench.state.state == "DISABLED"
    assert bench.enable().state == "READY"
    state, fault = bench.disconnect(2)
    assert state.state == "SAFE_DISCONNECTED" and fault.safe_state_applied
    assert bench.recover().state == "READY"
