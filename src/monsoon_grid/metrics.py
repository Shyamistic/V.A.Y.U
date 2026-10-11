"""Metric registry: the single defining module for every emitted metric.

``build_metric`` is the only legal constructor of a ``MetricValue`` outside the
contract definition itself.  It reads unit, unit justification, and provenance
label from the one ``MetricSpec`` registered for a name, so a caller cannot
supply a wrong unit and two artifacts cannot disagree on a metric's labelling.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import MetricValue, Provenance, ProvenanceLabel, Unit


@dataclass(frozen=True)
class MetricSpec:
    name: str
    unit: Unit | None
    unit_justification: str | None
    provenance_label: ProvenanceLabel
    meaning: str
    computation: str


def _spec(name, unit, label, meaning, computation, *, unit_justification=None) -> MetricSpec:
    return MetricSpec(
        name=name,
        unit=unit,
        unit_justification=unit_justification,
        provenance_label=label,
        meaning=meaning,
        computation=computation,
    )


_CONTRACTED_SERVICE_FRACTION_MEANING = (
    "Fraction of contracted flexible energy demand actually served over the 96-slot replay. "
    "Numerator sums, over slots and contracted load classes, "
    "(demand - shed - max(0, shift_down - shift_up) - unserved_share) * DT; denominator sums demand * DT. "
    "1.0 means no contracted flexibility was exercised and no contracted load went unserved; "
    "0.0 means none of the contracted flexible demand was served. "
    "If no contracted flexible load exists the metric is unavailable."
)

_DSM_ASSUMPTION = (
    "Computed from assumed slab rate_multipliers under the modelled CERC DSM mechanism; "
    "the rates are scenario assumptions, not regulatory fact."
)

_OBLIGATION_NOTE = (
    "Heuristic placeholder; the principled value is a dual variable from a multistage stochastic programme. "
    "Nothing in this phase consumes it."
)

METRIC_REGISTRY: dict[str, MetricSpec] = {
    "total_import_mwh": _spec("total_import_mwh", "MWh", "derived", "Total grid energy imported over the replay.", "sum of import_mw * DT across slots"),
    "total_export_mwh": _spec("total_export_mwh", "MWh", "derived", "Total grid energy exported over the replay.", "sum of export_mw * DT across slots"),
    "net_cost_usd": _spec("net_cost_usd", "USD", "derived", "Replay net cost; the sum of per-slot cost_usd.", "sum of TwinSlotState.cost_usd across slots"),
    "renewable_utilization_fraction": _spec("renewable_utilization_fraction", "fraction", "derived", "Dispatched renewable energy over available renewable energy.", "total_dispatched_mwh / total_available_mwh"),
    "renewable_curtailment_mwh": _spec("renewable_curtailment_mwh", "MWh", "derived", "Available renewable energy not dispatched.", "sum of max(0, available - dispatch) * DT"),
    "carbon_kgco2e": _spec("carbon_kgco2e", "kgCO2e", "derived", "Total emissions from grid import.", "sum of import_mw * DT * carbon_factor"),
    "carbon_factor_kgco2e_per_mwh": _spec("carbon_factor_kgco2e_per_mwh", "kgCO2e_per_MWh", "scenario_assumption", "Assumed grid carbon intensity.", "carbon_factor fixture value"),
    "critical_unserved_mwh": _spec("critical_unserved_mwh", "MWh", "derived", "Twin-realized critical demand not served.", "sum of per-slot critical_unserved_mwh"),
    "noncritical_unserved_mwh": _spec("noncritical_unserved_mwh", "MWh", "derived", "Twin-realized non-critical demand not served.", "sum of per-slot noncritical_unserved_mwh"),
    "validator_predicted_critical_unserved_mwh": _spec("validator_predicted_critical_unserved_mwh", "MWh", "derived", "Sum of SafetyDecision.critical_unserved_mwh across decisions.", "sum over decisions"),
    "reserve_shortfall_mwh": _spec("reserve_shortfall_mwh", "MWh", "derived", "Total BESS reserve shortfall over the replay.", "sum of per-slot reserve shortfall"),
    "bess_throughput_mwh": _spec("bess_throughput_mwh", "MWh", "derived", "Total BESS charge+discharge throughput.", "sum of (charge+discharge) * DT"),
    "planned_fallback_count": _spec("planned_fallback_count", "count", "derived", "Slots whose planner proposal was non-OPTIMAL.", "count over planned slots"),
    "applied_fallback_count": _spec("applied_fallback_count", "count", "derived", "Slots where a non-OPTIMAL proposal was applied by the twin.", "count over applied slots"),
    "safety_filter_interventions": _spec("safety_filter_interventions", "count", "derived", "Slots where a proposal and decision existed but the twin declined to apply.", "count over slots"),
    "unapplied_slots_with_unserved_count": _spec("unapplied_slots_with_unserved_count", "count", "derived", "Unapplied slots that report positive unserved energy.", "count over unapplied slots"),
    "approval_events": _spec("approval_events", "count", "derived", "Count of applied slots carrying an approval.", "count over applied slots with an approval"),
    "trace_complete": _spec("trace_complete", "count", "derived", "1 when all 96 slot states were produced, else 0.", "1 if len(slot_states)==96 else 0"),
    "dr_shed_mwh": _spec("dr_shed_mwh", "MWh", "derived", "Realized demand shed over the replay.", "sum of per-slot dr_shed_mwh"),
    "dr_shift_mwh": _spec("dr_shift_mwh", "MWh", "derived", "Realized demand shifted (deferred) over the replay.", "sum of per-slot dr_shift_mwh"),
    "dr_rebound_mwh": _spec("dr_rebound_mwh", "MWh", "derived", "Realized rebound energy over the replay.", "sum of per-slot dr_rebound_mwh"),
    "dr_cost_usd": _spec("dr_cost_usd", "USD", "derived", "Priced demand response cost over the replay.", "sum of per-slot dr_cost_usd"),
    "noncritical_reduction_mwh": _spec("noncritical_reduction_mwh", "MWh", "derived", "Voluntary bounded reduction of flexible non-critical load (unpriced; distinct from priced DR).", "sum of per-slot reduce_mw * DT"),
    "contracted_service_fraction": _spec("contracted_service_fraction", "fraction", "derived", _CONTRACTED_SERVICE_FRACTION_MEANING, "see meaning"),
    "market_revenue_usd": _spec("market_revenue_usd", "USD", "derived", "Delivered committed export revenue net of fee and loss.", "sum of per-slot market_revenue_usd"),
    "nonmarket_export_valuation_usd": _spec("nonmarket_export_valuation_usd", "USD", "derived", "Uncommitted export valued at the labelled non-market rate.", "sum of per-slot nonmarket_export_valuation_usd"),
    "market_shortfall_mwh": _spec("market_shortfall_mwh", "MWh", "derived", "Committed energy not delivered.", "sum of per-slot market_shortfall_mwh"),
    "market_shortfall_penalty_usd": _spec("market_shortfall_penalty_usd", "USD", "derived", "Shortfall penalty at commitment price plus shortfall penalty.", "sum of per-slot market_shortfall_penalty_usd"),
    "committed_export_mwh": _spec("committed_export_mwh", "MWh", "derived", "Delivered committed export energy.", "sum of committed_export_mw * DT"),
    "uncommitted_export_mwh": _spec("uncommitted_export_mwh", "MWh", "derived", "Uncommitted export energy.", "sum of uncommitted_export_mw * DT"),
    "planner_latency_p50_seconds": _spec("planner_latency_p50_seconds", "seconds", "measured", "Median total planner latency across slots.", "nearest-rank p50 over total-phase samples"),
    "planner_latency_p95_seconds": _spec("planner_latency_p95_seconds", "seconds", "measured", "95th percentile total planner latency across slots.", "nearest-rank p95 over total-phase samples"),
    "replay_wall_seconds": _spec("replay_wall_seconds", "seconds", "measured", "Measured wall-clock duration of the replay.", "time.monotonic delta"),
    "dsm_exposure_usd_assumed_rates": _spec("dsm_exposure_usd_assumed_rates", "USD", "derived", f"Total DSM charge over the replay. {_DSM_ASSUMPTION}", "sum of DsmChargeResult.charge_usd"),
    "dsm_exposure_delta_usd_assumed_rates": _spec("dsm_exposure_delta_usd_assumed_rates", "USD", "derived", f"Post-2026-04-01 minus pre-2026-04-01 DSM exposure on identical inputs. {_DSM_ASSUMPTION}", "difference of two regime exposures"),
    "obligation_shadow_price_usd_per_mwh": _spec("obligation_shadow_price_usd_per_mwh", "USD_per_MWh", "derived", f"Portfolio obligation shadow price (max across obligations). {_OBLIGATION_NOTE}", "max of per-obligation shadow prices"),
    "obligation_projected_attainment_fraction": _spec("obligation_projected_attainment_fraction", "fraction", "derived", f"Projected obligation attainment fraction. {_OBLIGATION_NOTE}", "see ObligationTracker"),
}


def build_metric(name: str, value: float | int | None, *, status: str = "available", reason: str | None = None) -> MetricValue:
    """Construct the one legal MetricValue for a registered name.

    Raises KeyError for an unregistered name, which makes the registry the only
    possible source of an emitted metric.
    """
    spec = METRIC_REGISTRY[name]
    return MetricValue(
        name=name,
        value=value,
        unit=spec.unit,
        unit_justification=spec.unit_justification,
        status=status,
        reason=reason,
        provenance=Provenance(
            label=spec.provenance_label,
            source_id="metric-registry",
            source_version="metric-registry-v1",
            input_refs=[name] if spec.provenance_label == "derived" else [],
        ),
    )
