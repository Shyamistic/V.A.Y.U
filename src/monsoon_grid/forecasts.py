"""Deterministic baseline P10/P50/P90 forecast bands."""

from __future__ import annotations

import datetime as dt

from .canonical import sha256_hex
from .contracts import ForecastBand, LoadClassSpec, PortfolioFixture, Provenance, SolarFarmSpec, WindFarmSpec

FORECAST_VERSION = "baseline-quantile-v1"


def _prov(*refs: str) -> Provenance:
    return Provenance(label="derived", source_id="baseline-forecast", source_version=FORECAST_VERSION, input_refs=list(refs) or ["forecast"])


def build_forecasts(portfolio: PortfolioFixture) -> list[ForecastBand]:
    start = portfolio.fixture_start_utc
    end = start + dt.timedelta(minutes=15 * 96)
    output: list[ForecastBand] = []
    for asset in sorted(portfolio.assets, key=lambda item: item.asset_id):
        if isinstance(asset, (SolarFarmSpec, WindFarmSpec)):
            base = portfolio.profiles_mw[asset.profile_id]
            p10 = [round(max(0.0, value * asset.capacity_mw * 0.72), 6) for value in base]
            p50 = [round(max(0.0, value * asset.capacity_mw), 6) for value in base]
            p90 = [round(min(asset.capacity_mw, value * asset.capacity_mw * 1.08), 6) for value in base]
            variable = "solar" if isinstance(asset, SolarFarmSpec) else "wind"
            output.append(ForecastBand(forecast_id=f"{asset.asset_id}:forecast", variable=variable, asset_id=asset.asset_id, issue_time=start, valid_start=start, valid_end=end, unit="MW", p10=p10, p50=p50, p90=p90, model_version=FORECAST_VERSION, scenario_lineage="scenario-02", provenance=_prov(asset.record_id or asset.asset_id)))
        elif isinstance(asset, LoadClassSpec):
            base = portfolio.profiles_mw[asset.base_profile_id]
            output.append(ForecastBand(forecast_id=f"{asset.asset_id}:forecast", variable="demand", asset_id=asset.asset_id, issue_time=start, valid_start=start, valid_end=end, unit="MW", p10=[round(value * 0.92, 6) for value in base], p50=[round(value, 6) for value in base], p90=[round(value * 1.08, 6) for value in base], model_version=FORECAST_VERSION, scenario_lineage="scenario-02", provenance=_prov(asset.record_id or asset.asset_id)))
    price = portfolio.profiles_mw.get("price", [70.0] * portfolio.slot_count)
    output.append(ForecastBand(forecast_id="price:forecast", variable="price", asset_id=None, issue_time=start, valid_start=start, valid_end=end, unit="USD_per_MWh", p10=[round(value * 0.70, 6) for value in price], p50=[round(value, 6) for value in price], p90=[round(value * 1.35, 6) for value in price], model_version=FORECAST_VERSION, scenario_lineage="scenario-02", provenance=_prov(portfolio.record_id or "portfolio")))
    return output


def forecast_set_hash(portfolio: PortfolioFixture) -> str:
    return sha256_hex(build_forecasts(portfolio))
