"""D5: the India site fixture validates as SitePortfolio, not as PortfolioFixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from monsoon_grid.contracts import PortfolioFixture, SitePortfolio

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "india_site_portfolio.json"


def _raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_validates_as_site_portfolio():
    sp = SitePortfolio.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert sp.country == "IN"
    assert len(sp.sites) == 4
    assert set(sp.dsm_classification) == {s.asset_id for s in sp.sites}


def test_coordinates_measured_commercials_assumption():
    sp = SitePortfolio.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    for site in sp.sites:
        assert site.provenance.label == "measured"  # coordinates are traceable
    for ppa in sp.ppa_terms:
        assert ppa.provenance.label == "scenario_assumption"
    for warranty in sp.storage_warranties:
        assert warranty.provenance.label == "scenario_assumption"


def test_cannot_validate_as_portfolio_fixture():
    # The India fixture is site metadata, not a replayable 5/3/2/3 roster portfolio.
    with pytest.raises(ValidationError):
        PortfolioFixture.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_not_in_fixture_set_files():
    from monsoon_grid.config import FIXTURE_SET_FILES

    assert "india_site_portfolio.json" not in FIXTURE_SET_FILES
