import os

from monsoon_grid.certificates import create_certificate, ensure_demo_trust, verify_certificate
from monsoon_grid.config import Settings
from monsoon_grid.persistence import PersistenceStore


def test_ed25519_certificate_round_trip(tmp_path):
    keyring, seed, fingerprint = ensure_demo_trust()
    os.environ["MONSOON_GRID_TRUST_KEYRING"] = str(keyring)
    os.environ["MONSOON_GRID_TRUST_FINGERPRINT"] = fingerprint
    os.environ["MONSOON_GRID_SIGNING_KEY"] = str(seed)
    store = PersistenceStore(tmp_path)
    certificate = create_certificate(replay_id="r", decision_id="d", decision_status="REJECTED", evidence={"replay_version": "monsoon-grid-replay-1"}, objects={"input": {"value": 1}}, settings=Settings.from_env(), store=store)
    assert verify_certificate(certificate, settings=Settings.from_env(), store=store)["verified"]
