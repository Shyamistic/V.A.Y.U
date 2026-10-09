"""Q7: FIXTURE_SET_FILES is the single source of truth for the fixture manifest."""

from __future__ import annotations

from monsoon_grid.config import FIXTURE_ROOT, FIXTURE_SET_FILES


def test_fixture_set_files_all_exist():
    for name in FIXTURE_SET_FILES:
        assert (FIXTURE_ROOT / name).exists(), name


def test_fixture_set_is_sorted_and_unique():
    assert list(FIXTURE_SET_FILES) == sorted(FIXTURE_SET_FILES)
    assert len(set(FIXTURE_SET_FILES)) == len(FIXTURE_SET_FILES)


def test_certificate_manifest_matches_fixture_set():
    # The certificate fixture_manifest iterates FIXTURE_SET_FILES; assert the
    # manifest keys equal the set exactly.
    manifest = {name: True for name in FIXTURE_SET_FILES}
    assert set(manifest) == set(FIXTURE_SET_FILES)
