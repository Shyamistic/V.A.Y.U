"""Tests for the typed integration boundary (design §9)."""

from __future__ import annotations

import datetime as dt
import importlib
import pkgutil

import pytest
from pydantic import ValidationError

import monsoon_grid.forecasting as fpkg
from monsoon_grid.forecasting import interface as I


def _attestation(status="tier_a_external", caveats=None, reviewed=True):
    return I.CoverageAttestation(
        nominal_coverage=0.8,
        quantile_levels=[0.1, 0.5, 0.9],
        empirical_coverage_by_horizon={"h_4step_1h": 0.79},
        mean_width_by_horizon={"h_4step_1h": 12.0},
        empirical_coverage_by_regime={"clear": 0.8},
        mean_width_by_regime={"clear": 10.0},
        sample_count_by_cell={"h_4step_1h|clear": 500},
        regime_presence_counts={"clear": 500},
        calibration_cell_fallback=[],
        measured_on_dataset_id="emsx_site1",
        measured_on_dataset_sha256="a" * 64,
        baseline_source="provider_forecast",
        validation_tier="A",
        validation_status=status,
        caveats=caveats if caveats is not None else ["hourly block is not the 15-min CERC block"],
        caveats_reviewed=reviewed,
    )


def test_library_import_surface_no_contracts_or_dsm():
    # No module under forecasting/ (excluding tests) may import contracts or dsm.
    for mod in pkgutil.walk_packages(fpkg.__path__, prefix="monsoon_grid.forecasting."):
        m = importlib.import_module(mod.name)
        src_file = getattr(m, "__file__", "") or ""
        if not src_file.endswith(".py"):
            continue
        import inspect

        try:
            src = inspect.getsource(m)
        except OSError:
            continue
        assert "import monsoon_grid.contracts" not in src
        assert "from monsoon_grid.contracts" not in src
        assert "monsoon_grid.dsm" not in src


def test_validated_true_for_tier_a_reviewed():
    att = _attestation()
    assert att.validated is True


def test_validated_false_for_tier_b():
    att = _attestation(status="tier_b_pseudo_truth")
    assert att.validated is False


def test_validated_false_if_not_reviewed():
    att = _attestation(reviewed=False)
    assert att.validated is False


def test_caveats_required_when_status_not_none():
    with pytest.raises(ValidationError):
        _attestation(caveats=[])


def test_coverage_without_width_rejected():
    with pytest.raises(ValidationError):
        I.CoverageAttestation(
            nominal_coverage=0.8,
            quantile_levels=[0.1, 0.9],
            empirical_coverage_by_horizon={"h1": 0.8},
            mean_width_by_horizon={},  # missing width for h1
            empirical_coverage_by_regime={},
            mean_width_by_regime={},
            sample_count_by_cell={},
            regime_presence_counts={},
            calibration_cell_fallback=[],
            measured_on_dataset_id="x",
            measured_on_dataset_sha256="a" * 64,
            baseline_source="climatology",
            validation_tier="A",
            validation_status="none",
            caveats=[],
        )


def test_round_rearrange_validate_ties_legal():
    # Sub-1e-6 differences collapse to the same rounded value (tie) -> legal.
    q = [[0.1234561], [0.1234562], [0.5]]
    out = I.round_rearrange_validate(q)
    assert out[0][0] == out[1][0]  # collapsed to a tie
    assert out[2][0] == 0.5


def test_round_rearrange_validate_crossing_raises():
    with pytest.raises(ValueError):
        I.round_rearrange_validate([[0.9], [0.1]])  # decreasing -> cross


def _band(**over):
    base = dict(
        band_id="b1",
        asset_id="pv1",
        variable="solar_availability",
        issue_time=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        valid_start=dt.datetime(2024, 6, 2, tzinfo=dt.timezone.utc),
        valid_end=dt.datetime(2024, 6, 2, 0, 15, tzinfo=dt.timezone.utc),
        slot_minutes=15,
        quantile_levels=[0.1, 0.5, 0.9],
        quantiles=[[1.0], [2.0], [3.0]],
        point_p50_mw=[2.0],
        band_source=["model"],
        baseline_mw=[1.5],
        baseline_source="provider_forecast",
        model_version="qr-gbm-v1",
        calibration_version="enbpi-v1",
        calibration_method="enbpi",
        model_artifact_sha256="a" * 64,
        calibration_artifact_sha256="b" * 64,
        coverage_attestation=_attestation(),
    )
    base.update(over)
    return I.CalibratedBand(**base)


def test_band_constructs_and_validates():
    band = _band()
    assert band.unit == "MW"
    assert band.coverage_attestation.validated is True


def test_band_band_source_length_must_match_slots():
    with pytest.raises(ValidationError):
        _band(band_source=["model", "model"])  # 2 but n_slots inferred from this = 2
    # length mismatch between band_source and quantiles columns
    with pytest.raises(ValidationError):
        _band(band_source=["model", "model"], quantiles=[[1.0], [2.0], [3.0]])


def test_band_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _band(issue_time=dt.datetime(2024, 6, 1))  # naive


def test_band_issue_after_valid_rejected():
    with pytest.raises(ValidationError):
        _band(
            issue_time=dt.datetime(2024, 6, 3, tzinfo=dt.timezone.utc),
            valid_start=dt.datetime(2024, 6, 2, tzinfo=dt.timezone.utc),
        )


def test_six_decimal_rule_enforced():
    with pytest.raises(ValidationError):
        _band(baseline_mw=[1.12345678])  # too many decimals


def test_band_quantiles_crossing_rejected():
    with pytest.raises(ValidationError):
        _band(quantiles=[[3.0], [2.0], [1.0]])  # decreasing in tau


def test_band_request_tau_order_and_tz():
    req = I.BandRequest(
        issue_time=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        valid_start=dt.datetime(2024, 6, 2, tzinfo=dt.timezone.utc),
        valid_end=dt.datetime(2024, 6, 3, tzinfo=dt.timezone.utc),
        slot_minutes=15,
        asset_id="pv1",
        variable="solar_availability",
        capacity_mw=100.0,
        capacity_source="scenario_assumption",
        quantile_levels=[0.1, 0.5, 0.9],
        nominal_coverage=0.8,
        model_artifact_id="m1",
        calibration_artifact_id="c1",
    )
    assert req.offline is False
    with pytest.raises(ValidationError):
        I.BandRequest(
            issue_time=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
            valid_start=dt.datetime(2024, 6, 2, tzinfo=dt.timezone.utc),
            valid_end=dt.datetime(2024, 6, 3, tzinfo=dt.timezone.utc),
            slot_minutes=15,
            asset_id="pv1",
            variable="v",
            capacity_mw=100.0,
            capacity_source="x",
            quantile_levels=[0.9, 0.1],  # not increasing
            nominal_coverage=0.8,
            model_artifact_id="m1",
            calibration_artifact_id="c1",
        )


def test_band_fields_compatible_with_contracts_forecastband():
    # contracts imported in TEST ONLY to assert field-level compatibility.
    from monsoon_grid import contracts

    assert hasattr(contracts, "ID_RE")
    # Our band ids obey the product id regex.
    assert contracts.ID_RE.fullmatch(_band().asset_id)
