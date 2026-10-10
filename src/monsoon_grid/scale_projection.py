"""Deterministic scale projection: unit -> city/regional, and hardware-node topology.

Everything here is a DECLARED PROJECTION derived arithmetically from the validated
single-unit demo fixture and its 96-slot replay. It is NOT new telemetry, not a
second simulation, and not a validated multi-site study. The projection multiplies
the real unit by a declared integer fleet count and reports, honestly, which
properties are preserved under linear replication and which require a genuine
multi-period, network-aware study that is NOT yet done.

The hardware-node topology is likewise a PROPOSAL: it maps the real asset roster
onto a per-site edge-node / gateway streaming design built around the existing
pure-software bench emulator. No device is connected; no node is validated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import BessSpec, LoadClassSpec, PortfolioFixture, SolarFarmSpec, WindFarmSpec

# Declared fleet multiplier for the "city / regional" projection. A city-scale
# operator in the problem statement runs many such industrial portfolios; we
# project the validated unit by this integer and label every projected number.
CITY_FLEET_UNITS = 12


@dataclass(frozen=True)
class ScaleRow:
    """One quantity compared across unit scale and projected city scale."""

    metric: str
    unit_value: float
    city_value: float
    unit: str
    preserved_under_replication: bool
    note: str


@dataclass(frozen=True)
class HardwareNode:
    """A proposed edge/gateway node in the streaming topology (not built)."""

    node_id: str
    role: str  # site_edge | aggregation_gateway | operator_console
    covers: list[str]
    sensors: list[str]
    uplink: str
    note: str


@dataclass(frozen=True)
class ScaleProjection:
    fleet_units: int
    unit_asset_counts: dict[str, int]
    city_asset_counts: dict[str, int]
    comparison: list[dict[str, Any]]
    hardware_nodes: list[dict[str, Any]]
    preserved_properties: list[str]
    requires_restudy: list[str]
    disclaimer: str


def _asset_counts(portfolio: PortfolioFixture) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in portfolio.assets:
        counts[asset.kind] = counts.get(asset.kind, 0) + 1
    return counts


def _installed_capacity_mw(portfolio: PortfolioFixture) -> float:
    total = 0.0
    for asset in portfolio.assets:
        if isinstance(asset, (SolarFarmSpec, WindFarmSpec)):
            total += asset.capacity_mw
    return round(total, 6)


def _storage_energy_mwh(portfolio: PortfolioFixture) -> float:
    return round(sum(asset.capacity_mwh for asset in portfolio.assets if isinstance(asset, BessSpec)), 6)


def build_scale_projection(
    portfolio: PortfolioFixture,
    metrics: dict[str, Any],
    fleet_units: int = CITY_FLEET_UNITS,
) -> ScaleProjection:
    """Derive the unit->city comparison and hardware-node topology.

    ``metrics`` is the already-computed, signed replay metric payload; only its
    values are read, never recomputed, so the projection cannot drift from the
    validated unit result.
    """
    unit_counts = _asset_counts(portfolio)
    city_counts = {kind: count * fleet_units for kind, count in unit_counts.items()}

    def metric_value(name: str) -> float:
        for row in metrics.get("values", []):
            if row.get("name") == name and isinstance(row.get("value"), (int, float)):
                return float(row["value"])
        return 0.0

    installed_mw = _installed_capacity_mw(portfolio)
    storage_mwh = _storage_energy_mwh(portfolio)
    net_cost = metric_value("net_cost_usd")
    critical_unserved = metric_value("critical_unserved_mwh")
    dr_cost = metric_value("dr_cost_usd")

    rows = [
        ScaleRow("Installed generation", installed_mw, round(installed_mw * fleet_units, 6), "MW", True,
                 "Nameplate scales linearly; simultaneity and local network limits do not."),
        ScaleRow("Storage energy", storage_mwh, round(storage_mwh * fleet_units, 6), "MWh", True,
                 "Energy capacity replicates; shared reserve policy across sites needs a joint study."),
        ScaleRow("Net replay cost", net_cost, round(net_cost * fleet_units, 6), "USD", False,
                 "Linear projection ignores inter-site arbitrage, shared market exposure and congestion."),
        ScaleRow("Critical unserved energy", critical_unserved, round(critical_unserved * fleet_units, 6), "MWh", False,
                 "Reliability does NOT scale linearly; correlated weather raises joint risk."),
        ScaleRow("Priced demand response", dr_cost, round(dr_cost * fleet_units, 6), "USD", False,
                 "Aggregated DR has rebound and market-depth limits a per-unit sum omits."),
    ]

    nodes: list[HardwareNode] = []
    solar = [a.asset_id for a in portfolio.assets if isinstance(a, SolarFarmSpec)]
    wind = [a.asset_id for a in portfolio.assets if isinstance(a, WindFarmSpec)]
    bess = [a.asset_id for a in portfolio.assets if isinstance(a, BessSpec)]
    loads = [a.asset_id for a in portfolio.assets if isinstance(a, LoadClassSpec)]

    nodes.append(HardwareNode(
        "edge-solar", "site_edge", solar,
        ["irradiance (lux proxy)", "string voltage", "string current", "cell temperature"],
        "MQTT/TLS -> aggregation_gateway",
        "One ESP32-class node per solar field; telemetry-only, never an authoritative dispatch input by default.",
    ))
    nodes.append(HardwareNode(
        "edge-wind", "site_edge", wind,
        ["nacelle anemometer", "phase current", "vibration (proxy)"],
        "MQTT/TLS -> aggregation_gateway",
        "Per-wind-farm node; curtailment command path is NOT implemented and would require an OT assurance program.",
    ))
    nodes.append(HardwareNode(
        "edge-bess", "site_edge", bess,
        ["pack voltage", "pack current", "cell temperature", "contactor state"],
        "MQTT/TLS -> aggregation_gateway",
        "BESS node mirrors the software bench droop/current-limit interlocks; reserve is enforced locally, not from cloud.",
    ))
    nodes.append(HardwareNode(
        "edge-load", "site_edge", loads,
        ["feeder power", "relay state", "DR opt-in flag"],
        "MQTT/TLS -> aggregation_gateway",
        "Flexible-load relays follow priced-DR contracts; the critical feeder relay is physically non-sheddable.",
    ))
    nodes.append(HardwareNode(
        "gateway-01", "aggregation_gateway", ["edge-solar", "edge-wind", "edge-bess", "edge-load"],
        ["frame identity", "sequence/age", "unit sanity", "duplicate detection"],
        "authenticated batch -> operator console / Cloud ingest",
        "A single Pi-class gateway validates device identity, units, ordering and freshness before any export.",
    ))
    nodes.append(HardwareNode(
        "console-01", "operator_console", ["gateway-01"],
        ["replay evidence", "approval workflow", "certificate verification"],
        "operator action over authenticated API",
        "The console runs the same validated software stack; approval and safety stay in software, never on the edge alone.",
    ))

    return ScaleProjection(
        fleet_units=fleet_units,
        unit_asset_counts=unit_counts,
        city_asset_counts=city_counts,
        comparison=[asdict(row) for row in rows],
        hardware_nodes=[asdict(node) for node in nodes],
        preserved_properties=[
            "Per-slot energy balance and reserve invariants hold per replicated unit.",
            "Independent validator + separate approval remain mandatory at every site.",
            "Signed, replayable evidence is produced per unit and can be aggregated.",
        ],
        requires_restudy=[
            "Shared transmission/congestion limits across sites (no network model yet).",
            "Correlated weather raising joint reliability risk (needs a multi-site stochastic study).",
            "Aggregated market depth and inter-site arbitrage (single-unit market model only).",
            "The open G3 non-anticipativity gap compounds across a coordinated fleet.",
        ],
        disclaimer=(
            "DECLARED LINEAR PROJECTION from one validated unit x "
            f"{fleet_units}. Not a validated multi-site simulation. Reliability, cost "
            "and market figures at city scale require a genuine network-aware study."
        ),
    )


__all__ = ["CITY_FLEET_UNITS", "ScaleProjection", "build_scale_projection"]
