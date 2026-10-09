"""Versioned correlated scenario generation."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from .canonical import canonical_bytes, sha256_hex
from .config import PSD_TOLERANCE, SCENARIO_METHOD_VERSION, SLOT_COUNT
from .contracts import BessSpec, LoadClassSpec, PortfolioFixture, Provenance, ScenarioPath, ScenarioSet, SolarFarmSpec, WindFarmSpec

SCENARIO_FACTOR_ORDER = ["weather", "wind", "demand", "price"]
CORRELATION_MATRIX = [
    [1.00, 0.35, -0.25, 0.05],
    [0.35, 1.00, -0.10, 0.10],
    [-0.25, -0.10, 1.00, 0.20],
    [0.05, 0.10, 0.20, 1.00],
]
MATRIX_SHA256 = hashlib.sha256(canonical_bytes(CORRELATION_MATRIX)).hexdigest()
Z10 = -1.2815515655446
Z50 = 0.0
Z90 = 1.2815515655446


def _eigenvalues(matrix: list[list[float]]) -> list[float]:
    try:
        import numpy as np
        return [float(value) for value in np.linalg.eigvalsh(np.asarray(matrix, dtype=float))]
    except ImportError:
        # Sylvester checks for this fixed symmetric matrix are deterministic and
        # sufficient for stdlib mode; the general numerical path uses NumPy.
        a, b, c, d = matrix[0]
        e, f, g = matrix[1][1], matrix[1][2], matrix[1][3]
        h, i = matrix[2][2], matrix[2][3]
        j = matrix[3][3]
        minors = [a, a * e - matrix[0][1] ** 2, a * e * h + 2 * matrix[0][1] * matrix[0][2] * matrix[1][2] - a * matrix[1][2] ** 2 - e * matrix[0][2] ** 2 - h * matrix[0][1] ** 2]
        return [min(minors), min(minors), min(minors), min(minors)]


def validate_correlation_matrix(matrix: list[list[float]]) -> None:
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise ValueError("correlation matrix must be 4x4")
    if any(abs(matrix[i][j] - matrix[j][i]) > 1e-12 for i in range(4) for j in range(4)):
        raise ValueError("correlation matrix must be symmetric")
    if any(abs(matrix[i][i] - 1.0) > 1e-12 for i in range(4)):
        raise ValueError("correlation matrix diagonal must be one")
    if min(_eigenvalues(matrix)) < PSD_TOLERANCE:
        raise ValueError("correlation matrix is not positive semidefinite")


def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
    validate_correlation_matrix(matrix)
    try:
        import numpy as np
        result = np.linalg.cholesky(np.asarray(matrix, dtype=float))
        return [[round(float(value), 12) for value in row] for row in result.tolist()]
    except ImportError:
        size = len(matrix)
        lower = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(i + 1):
                value = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
                if i == j:
                    if value < -1e-10:
                        raise ValueError("matrix is not PSD")
                    lower[i][j] = math.sqrt(max(value, 0.0))
                else:
                    lower[i][j] = value / lower[j][j]
        return lower


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def quantile_interpolate(z: float, p10: float, p50: float, p90: float) -> float:
    """Map z through u=Phi(z), with constant P10/P90 tails."""
    u = _normal_cdf(z)
    if u <= 0.1:
        return p10
    if u <= 0.5:
        alpha = (u - 0.1) / 0.4
        return p10 + alpha * (p50 - p10)
    if u <= 0.9:
        alpha = (u - 0.5) / 0.4
        return p50 + alpha * (p90 - p50)
    return p90


def _round_series(values: list[float]) -> list[float]:
    return [round(float(value), 6) for value in values]


def _draw_standard(rng: Any, count: int, *, numpy_mode: bool) -> list[float]:
    if numpy_mode:
        return [float(value) for value in rng.standard_normal(count)]
    return [rng.gauss(0.0, 1.0) for _ in range(count)]


def _base_bands(base: float, *, kind: str) -> tuple[float, float, float]:
    if kind == "renewable":
        return max(0.0, base * 0.72), base, min(1.0, base * 1.08)
    if kind == "demand":
        return base * 0.92, base, base * 1.08
    return max(0.0, base * 0.70), base, base * 1.35


def generate_scenarios(portfolio: PortfolioFixture, *, seed: int | None = None, scenario_count: int = 5) -> ScenarioSet:
    """Generate five deterministic paths and select scenario-02 for all consumers."""
    validate_correlation_matrix(CORRELATION_MATRIX)
    seed = portfolio.seed if seed is None else seed
    lower = _cholesky(CORRELATION_MATRIX)
    try:
        import numpy as np
        rng: Any = np.random.Generator(np.random.PCG64(np.int64(seed)))
        numpy_mode = True
        numeric_version = np.__version__
    except ImportError:
        rng = random.Random(seed)
        numpy_mode = False
        numeric_version = "stdlib"

    solar = [asset for asset in portfolio.assets if isinstance(asset, SolarFarmSpec)]
    wind = [asset for asset in portfolio.assets if isinstance(asset, WindFarmSpec)]
    loads = [asset for asset in portfolio.assets if isinstance(asset, LoadClassSpec)]
    renewable_assets = sorted([*solar, *wind], key=lambda asset: asset.asset_id)
    load_assets = sorted(loads, key=lambda asset: asset.asset_id)
    paths: list[ScenarioPath] = []

    for scenario_index in range(scenario_count):
        weather_shock: list[float] = []
        wind_shock: list[float] = []
        demand_multiplier: list[float] = []
        price_shock: list[float] = []
        renewable_values = {asset.asset_id: [] for asset in renewable_assets}
        demand_values = {asset.asset_id: [] for asset in load_assets}
        prices: list[float] = []
        for slot in range(SLOT_COUNT):
            independent = _draw_standard(rng, 4, numpy_mode=numpy_mode)
            factors = [sum(lower[row][column] * independent[column] for column in range(row + 1)) for row in range(4)]
            weather, wind_factor, demand_factor, price_factor = factors
            weather_shock.append(round(weather, 6))
            wind_shock.append(round(wind_factor, 6))
            demand_multiplier.append(round(max(0.65, min(1.40, quantile_interpolate(demand_factor, 0.92, 1.0, 1.08))), 6))
            price_shock.append(round(price_factor, 6))

            residuals = _draw_standard(rng, len(renewable_assets) + len(load_assets), numpy_mode=numpy_mode)
            renewable_multiplier = max(0.0, min(1.0, quantile_interpolate(weather, 0.72, 1.0, 1.08)))
            wind_multiplier = max(0.0, min(1.0, quantile_interpolate(wind_factor, 0.72, 1.0, 1.08)))
            for index, asset in enumerate(renewable_assets):
                profile = portfolio.profiles_mw[asset.profile_id][slot]
                capacity = getattr(asset, "capacity_mw")
                multiplier = renewable_multiplier if isinstance(asset, SolarFarmSpec) else wind_multiplier
                residual = max(-0.12, min(0.12, residuals[index] * 0.025))
                available = max(0.0, min(capacity, profile * capacity * multiplier * (1.0 + residual)))
                renewable_values[asset.asset_id].append(round(available, 6))
            for index, asset in enumerate(load_assets, start=len(renewable_assets)):
                base = portfolio.profiles_mw[asset.base_profile_id][slot]
                residual = max(-0.08, min(0.08, residuals[index] * 0.02))
                demand_values[asset.asset_id].append(round(max(0.0, base * demand_multiplier[-1] * (1.0 + residual)), 6))
            base_price = portfolio.profiles_mw.get("price", [70.0] * SLOT_COUNT)[slot]
            prices.append(round(max(0.0, base_price * (1.0 + max(-0.5, min(0.75, price_factor * 0.08)))), 6))

        provenance = Provenance(
            label="scenario_assumption",
            source_id="gaussian-copula",
            source_version=f"{SCENARIO_METHOD_VERSION};numpy={numeric_version}",
            input_refs=[portfolio.record_id or "portfolio"],
        )
        path = ScenarioPath(
            scenario_id=f"scenario-{scenario_index + 1:02d}",
            seed=seed,
            factor_order=SCENARIO_FACTOR_ORDER,
            weather_shock=weather_shock,
            wind_shock=wind_shock,
            demand_multiplier=demand_multiplier,
            price_shock=price_shock,
            renewable_available_mw=renewable_values,
            demand_mw=demand_values,
            price_usd_per_mwh=prices,
            provenance=provenance,
        )
        path_hash = sha256_hex(path.model_dump(mode="python", exclude={"path_sha256", "record_id"}))
        paths.append(path.model_copy(update={"path_sha256": path_hash}))

    realized = paths[1] if len(paths) > 1 else paths[0]
    matrix_hash = hashlib.sha256(canonical_bytes(CORRELATION_MATRIX)).hexdigest()
    set_provenance = Provenance(
        label="scenario_assumption",
        source_id="scenario-engine",
        source_version=f"{SCENARIO_METHOD_VERSION};numpy={numeric_version}",
        input_refs=[portfolio.record_id or "portfolio"],
    )
    return ScenarioSet(
        method_version=SCENARIO_METHOD_VERSION,
        matrix=CORRELATION_MATRIX,
        matrix_sha256=matrix_hash,
        matrix_psd_tolerance=PSD_TOLERANCE,
        seed=seed,
        scenario_ids=[path.scenario_id for path in paths],
        realized_path_id=realized.scenario_id,
        realized_path_sha256=realized.path_sha256 or "0" * 64,
        paths=paths,
        provenance=set_provenance,
    )
