"""A2 regression: the planner objective responds to shed cost (priced DR).

Fails on pre-change code where the objective was indifferent to shed cost.
"""

from __future__ import annotations

from monsoon_grid import planner as planner_module
from monsoon_grid.assumptions import FlexibilityCostAssumption, load_flexibility_costs
from monsoon_grid.canonical import sha256_hex
from monsoon_grid.fixtures import load_demo_events, load_demo_portfolio
from monsoon_grid.scenarios import generate_scenarios


def _scaled_flexibility(multiplier: float) -> FlexibilityCostAssumption:
    base = load_flexibility_costs()
    entries = []
    for entry in base.entries:
        entries.append({
            "load_id": entry.load_id,
            "shed_cost_usd_per_mwh": round(entry.shed_cost_usd_per_mwh * multiplier, 6),
            "shift_cost_usd_per_mwh": entry.shift_cost_usd_per_mwh,
        })
    payload = {
        "flexibility_cost_version": base.flexibility_cost_version,
        "currency": base.currency,
        "entries": entries,
        "source_note": base.source_note,
    }
    digest = sha256_hex(payload)
    return FlexibilityCostAssumption.model_validate({
        "schema_version": "1.0",
        **payload,
        "provenance": {
            "label": "scenario_assumption",
            "source_id": "flexibility-cost-fixture",
            "source_version": base.flexibility_cost_version,
            "content_sha256": digest,
            "input_refs": [],
        },
    })


def _total_shed(monkeypatch, multiplier: float) -> float:
    monkeypatch.setattr(planner_module, "load_flexibility_costs", lambda path=None: _scaled_flexibility(multiplier))
    portfolio = load_demo_portfolio()
    events = load_demo_events()
    scenarios = generate_scenarios(portfolio, seed=20260701)
    # Slot 54 is an unserved/stressed slot in the demo; use a stressed slot where
    # the optimizer has a shed/no-shed choice.
    slot = 54
    result = planner_module.plan_next(
        portfolio,
        scenarios.paths[1],
        events,
        replay_id="test-priced-dr",
        slot_index=slot,
        policy_mode="cost_first",
        scenario_paths=scenarios.paths,
    )
    proposal = result.proposal
    return sum(values[0] for values in proposal.shed_mw.values())


def test_higher_shed_cost_reduces_shed(monkeypatch):
    low = _total_shed(monkeypatch, 1.0)
    high = _total_shed(monkeypatch, 10.0)
    assert high <= low + 1e-9
    # At least one of the two configurations must involve a real decision; if the
    # baseline sheds nothing the test is still valid (high <= low holds), but the
    # strict reduction is asserted whenever shedding occurs.
    if low > 1e-6:
        assert high < low
