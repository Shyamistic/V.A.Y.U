"""Ed25519 signed, content-addressed decision certificates."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes, sha256_hex
from .config import ARTIFACT_ROOT, PRODUCT_ROOT, TRUST_ROOT, Settings, ensure_runtime_dirs
from .contracts import Certificate
from .errors import CertificateError, DependencyUnavailableError, InputMissingError, ReplayVersionMismatchError, TrustAnchorError
from .persistence import PersistenceStore


TRUST_KEYRING_NAME = "verification-keyring.json"
TRUST_SEED_NAME = "demo-signing-seed.hex"
TRUST_FINGERPRINT_NAME = "verification-fingerprint.txt"


def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        return Ed25519PrivateKey, Ed25519PublicKey, Encoding, PublicFormat
    except ImportError as exc:
        raise DependencyUnavailableError("cryptography is required for signed certificate-producing replay") from exc


def _fingerprint(public_bytes: bytes) -> str:
    return hashlib.sha256(public_bytes).hexdigest()


def ensure_demo_trust() -> tuple[Path, Path, str]:
    """Return the isolated demo trust fixture outside mutable runtime state."""
    TRUST_ROOT.mkdir(parents=True, exist_ok=True)
    Ed25519PrivateKey, _, Encoding, PublicFormat = _crypto()
    seed_path = TRUST_ROOT / TRUST_SEED_NAME
    if not seed_path.exists():
        seed_path.write_text(bytes(range(32)).hex() + "\n", encoding="ascii")
    seed = bytes.fromhex(seed_path.read_text(encoding="ascii").strip())
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fingerprint = _fingerprint(public)
    keyring_path = TRUST_ROOT / TRUST_KEYRING_NAME
    expected = {"key_id": "demo-key-1", "public_key_base64": base64.b64encode(public).decode("ascii"), "fingerprint": fingerprint, "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2030-01-01T00:00:00Z", "revoked": False}
    keyring = {"schema_version": "1.0", "keys": [expected], "trust_anchor": "operator-pinned-demo-fixture"}
    if not keyring_path.exists() or json.loads(keyring_path.read_text(encoding="utf-8")) != keyring:
        keyring_path.write_text(json.dumps(keyring, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    fingerprint_path = TRUST_ROOT / TRUST_FINGERPRINT_NAME
    if not fingerprint_path.exists() or fingerprint_path.read_text(encoding="ascii").strip() != fingerprint:
        fingerprint_path.write_text(fingerprint + "\n", encoding="ascii")
    return keyring_path, seed_path, fingerprint


def demo_trust_environment() -> dict[str, str]:
    keyring, seed, fingerprint = ensure_demo_trust()
    return {
        "MONSOON_GRID_TRUST_KEYRING": str(keyring),
        "MONSOON_GRID_SIGNING_KEY": str(seed),
        "MONSOON_GRID_TRUST_FINGERPRINT": fingerprint,
    }


def _parse_time(value: str, field: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustAnchorError(f"trusted key {field} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise TrustAnchorError(f"trusted key {field} must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _trusted_key(settings: Settings):
    if settings.trust_keyring is None or not settings.trust_fingerprint:
        raise TrustAnchorError("an externally supplied keyring and pinned fingerprint are required")
    try:
        raw = json.loads(settings.trust_keyring.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrustAnchorError(f"trusted keyring is unreadable: {settings.trust_keyring}") from exc
    matches = [key for key in raw.get("keys", []) if key.get("fingerprint") == settings.trust_fingerprint]
    if len(matches) != 1 or matches[0].get("revoked"):
        raise TrustAnchorError("trusted fingerprint is absent or revoked")
    key = matches[0]
    try:
        public = base64.b64decode(key["public_key_base64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise TrustAnchorError("keyring public key is malformed") from exc
    if len(public) != 32 or _fingerprint(public) != settings.trust_fingerprint:
        raise TrustAnchorError("keyring fingerprint does not match public key")
    valid_at = _parse_time(settings.trust_valid_at, "trust_valid_at")
    if valid_at < _parse_time(str(key.get("valid_from")), "valid_from") or valid_at >= _parse_time(str(key.get("valid_until")), "valid_until"):
        raise TrustAnchorError("trusted key is outside its validity interval")
    return key, public


def _private_key(settings: Settings, keyring_key: dict[str, Any]):
    Ed25519PrivateKey, _, _, _ = _crypto()
    path_text = os.environ.get("MONSOON_GRID_SIGNING_KEY")
    if not path_text:
        raise TrustAnchorError("signing key is not available; set MONSOON_GRID_SIGNING_KEY")
    path = Path(path_text).resolve()
    try:
        seed = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise TrustAnchorError("signing key is not available or malformed") from exc
    if len(seed) != 32:
        raise TrustAnchorError("Ed25519 signing seed must be 32 bytes")
    private = Ed25519PrivateKey.from_private_bytes(seed)
    _, _, Encoding, PublicFormat = _crypto()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if _fingerprint(public) != keyring_key["fingerprint"]:
        raise TrustAnchorError("signing key does not match externally trusted keyring")
    return private


def _unsigned_payload(*, certificate_id: str, replay_id: str, decision_id: str, decision_status: str, input_manifest: dict[str, str], evidence: dict[str, Any], key_id: str, key_fingerprint: str) -> dict[str, Any]:
    return {"certificate_id": certificate_id, "replay_id": replay_id, "decision_id": decision_id, "decision_status": decision_status, "input_manifest": input_manifest, "evidence": evidence, "key_id": key_id, "key_fingerprint": key_fingerprint, "signature_algorithm": "Ed25519"}


def create_certificate(*, replay_id: str, decision_id: str, decision_status: str, evidence: dict[str, Any], objects: dict[str, Any], settings: Settings | None = None, certificate_id: str | None = None, store: PersistenceStore | None = None) -> Certificate:
    settings = settings or Settings.from_env()
    key, public = _trusted_key(settings)
    private = _private_key(settings, key)
    store = store or PersistenceStore()
    manifest = {name: store.put_object(value) for name, value in sorted(objects.items())}
    # Verify the complete manifest before signing so a partial content store can
    # never be represented as a successful certificate.
    for digest in manifest.values():
        store.get_object(digest)
    certificate_id = certificate_id or f"{replay_id}:certificate:{decision_id}"
    unsigned = _unsigned_payload(certificate_id=certificate_id, replay_id=replay_id, decision_id=decision_id, decision_status=decision_status, input_manifest=manifest, evidence=evidence, key_id=key["key_id"], key_fingerprint=key["fingerprint"])
    unsigned_digest = sha256_hex(unsigned)
    signature = private.sign(canonical_bytes(unsigned))
    certificate = Certificate(
        certificate_id=certificate_id,
        replay_id=replay_id,
        decision_id=decision_id,
        decision_status=decision_status,
        unsigned_digest=unsigned_digest,
        input_manifest=manifest,
        evidence={**evidence, "canonical_unsigned_payload": unsigned},
        key_id=key["key_id"],
        key_fingerprint=key["fingerprint"],
        signature_base64=base64.b64encode(signature).decode("ascii"),
        runtime_metadata={},
    )
    return certificate


def export_certificate(certificate: Certificate, *, path: Path | None = None, store: PersistenceStore | None = None) -> Path:
    store = store or PersistenceStore()
    target = path or (ARTIFACT_ROOT / "certificate.json")
    payload = canonical_bytes(certificate)
    store.export_atomic(payload, target)
    store.record_certificate(certificate=certificate.model_dump(mode="python"), manifest=certificate.input_manifest, artifact_path=target)
    return target


def verify_certificate(certificate: Certificate, *, settings: Settings | None = None, store: PersistenceStore | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    key, public = _trusted_key(settings)
    if certificate.key_fingerprint != key["fingerprint"] or certificate.key_id != key["key_id"]:
        raise TrustAnchorError("certificate key does not match externally pinned key")
    unsigned = certificate.evidence.get("canonical_unsigned_payload")
    if not isinstance(unsigned, dict):
        raise CertificateError("certificate is missing canonical unsigned payload")
    if sha256_hex(unsigned) != certificate.unsigned_digest:
        raise CertificateError("unsigned certificate digest mismatch")
    if unsigned.get("input_manifest") != certificate.input_manifest:
        raise CertificateError("certificate manifest mismatch")
    store = store or PersistenceStore()
    for name, digest in certificate.input_manifest.items():
        try:
            data = store.get_object(digest)
        except InputMissingError:
            raise
        if hashlib.sha256(data).hexdigest() != digest:
            raise InputMissingError(f"content-addressed evidence mismatch: {name}")
    try:
        _, Ed25519PublicKey, _, _ = _crypto()
        Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode(certificate.signature_base64, validate=True), canonical_bytes(unsigned))
    except InputMissingError:
        raise
    except Exception as exc:
        raise CertificateError("Ed25519 signature verification failed") from exc
    return {"verified": True, "certificate_id": certificate.certificate_id, "unsigned_digest": certificate.unsigned_digest, "key_fingerprint": certificate.key_fingerprint, "manifest_count": len(certificate.input_manifest)}


def load_and_verify(path: Path, *, settings: Settings | None = None, store: PersistenceStore | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        certificate = Certificate.model_validate_json(json.dumps(raw))
    except FileNotFoundError as exc:
        raise InputMissingError(f"certificate is missing: {path}") from exc
    except Exception as exc:
        raise CertificateError(f"certificate schema is invalid: {exc}") from exc
    store = store or PersistenceStore()
    result = verify_certificate(certificate, settings=settings, store=store)
    if not certificate.evidence.get("replay_version"):
        raise ReplayVersionMismatchError("certificate does not identify replay version")
    required = certificate.evidence.get("required_evidence", [])
    missing = [name for name in required if name not in certificate.input_manifest]
    if missing:
        raise ReplayVersionMismatchError(f"certificate evidence is incomplete: {', '.join(missing)}")
    artifact_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    consistency = store.certificate_consistency(certificate.certificate_id, artifact_digest=artifact_digest, manifest=certificate.input_manifest, unsigned_digest=certificate.unsigned_digest)
    if not consistency["consistent"]:
        raise InputMissingError(f"certificate persistence is inconsistent: {consistency['code']}")
    return {**result, "persistence_consistent": True, "offline_replay": certificate.evidence.get("offline_replay", False)}
