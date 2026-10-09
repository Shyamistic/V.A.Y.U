"""SQLite WAL state and content-addressed evidence persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes
from .config import STORE_ROOT, ensure_runtime_dirs
from .errors import InputMissingError


class PersistenceStore:
    def __init__(self, root: Path | None = None):
        ensure_runtime_dirs()
        self.root = root or STORE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sha256").mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "monsoon_grid.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS certificates (
                    certificate_id TEXT PRIMARY KEY,
                    replay_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    unsigned_digest TEXT NOT NULL,
                    artifact_path TEXT,
                    artifact_digest TEXT,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    certificate_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    approval_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    replay_id TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );
                """
            )
        self._migrate()

    def _migrate(self) -> None:
        """Idempotent column additions for audited approvals (design A5)."""
        wanted = {
            "replay_id": "TEXT NOT NULL DEFAULT ''",
            "decision_id": "TEXT NOT NULL DEFAULT ''",
            "slot_index": "INTEGER",
            "synthetic_approval": "INTEGER NOT NULL DEFAULT 0",
        }
        with self._connect() as connection:
            existing = {row[1] for row in connection.execute("PRAGMA table_info(approvals)").fetchall()}
            for column, decl in wanted.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE approvals ADD COLUMN {column} {decl}")

    def put_object(self, value: Any) -> str:
        data = canonical_bytes(value)
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / "sha256" / digest
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError(f"content-addressed object mismatch: {digest}")
        if not target.exists():
            temp = target.with_suffix(".tmp")
            with temp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        return digest

    def get_object(self, digest: str) -> bytes:
        target = self.root / "sha256" / digest
        if not target.exists():
            raise InputMissingError(f"content-addressed object is missing: {digest}")
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise InputMissingError(f"content-addressed object hash mismatch: {digest}")
        return data

    def record_certificate(self, *, certificate: dict[str, Any], manifest: dict[str, str], artifact_path: Path | None = None) -> None:
        certificate_id = str(certificate["certificate_id"])
        artifact_digest = None
        if artifact_path and artifact_path.exists():
            artifact_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO certificates(certificate_id,replay_id,decision_id,state,unsigned_digest,artifact_path,artifact_digest,manifest_json,created_at) VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                (certificate_id, certificate["replay_id"], certificate["decision_id"], certificate["decision_status"], certificate["unsigned_digest"], str(artifact_path) if artifact_path else None, artifact_digest, json.dumps(manifest, sort_keys=True)),
            )

    def record_approval_and_events(self, *, approval: dict[str, Any] | None, events: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            if approval:
                connection.execute(
                    "INSERT OR REPLACE INTO approvals(approval_id,certificate_id,state,actor_id,approval_json,replay_id,decision_id,slot_index,synthetic_approval) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        approval["approval_digest"],
                        approval.get("certificate_id", ""),
                        approval["state"],
                        approval["actor_id"],
                        json.dumps(approval, sort_keys=True),
                        approval.get("replay_id", ""),
                        approval.get("decision_id", ""),
                        approval.get("slot_index"),
                        1 if approval.get("synthetic_approval") else 0,
                    ),
                )
            for event in events:
                connection.execute("INSERT OR REPLACE INTO events(event_id,replay_id,event_json) VALUES(?,?,?)", (event["event_id"], event["replay_id"], json.dumps(event, sort_keys=True)))

    def load_approval(self, replay_id: str, decision_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT approval_json FROM approvals WHERE replay_id=? AND decision_id=? ORDER BY approval_id LIMIT 1",
                (replay_id, decision_id),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def remember_idempotency(self, key: str, request_digest: str, response: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO idempotency(idempotency_key,request_digest,response_json) VALUES(?,?,?)", (key, request_digest, json.dumps(response, sort_keys=True)))

    def get_idempotency(self, key: str) -> tuple[str, dict[str, Any]] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT request_digest,response_json FROM idempotency WHERE idempotency_key=?", (key,)).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))

    def recovery_scan(self) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        with self._connect() as connection:
            rows = connection.execute("SELECT certificate_id,artifact_path,artifact_digest FROM certificates").fetchall()
        for certificate_id, artifact_path, artifact_digest in rows:
            if not artifact_path or not Path(artifact_path).exists():
                issues.append({"certificate_id": certificate_id, "code": "ARTIFACT_MISSING"})
            elif artifact_digest and hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest() != artifact_digest:
                issues.append({"certificate_id": certificate_id, "code": "ARTIFACT_MISMATCH"})
        return issues

    def certificate_consistency(self, certificate_id: str, *, artifact_digest: str | None = None, manifest: dict[str, str] | None = None, unsigned_digest: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT unsigned_digest,artifact_path,artifact_digest,manifest_json FROM certificates WHERE certificate_id=?",
                (certificate_id,),
            ).fetchone()
        if row is None:
            return {"consistent": False, "code": "SQLITE_ROW_MISSING"}
        stored_unsigned, stored_path, stored_digest, manifest_json = row
        if unsigned_digest is not None and stored_unsigned != unsigned_digest:
            return {"consistent": False, "code": "UNSIGNED_DIGEST_MISMATCH"}
        if artifact_digest is not None and stored_digest != artifact_digest:
            return {"consistent": False, "code": "ARTIFACT_MISMATCH"}
        if stored_path and not Path(stored_path).exists():
            return {"consistent": False, "code": "ARTIFACT_MISSING"}
        try:
            stored_manifest = json.loads(manifest_json)
        except json.JSONDecodeError:
            return {"consistent": False, "code": "MANIFEST_MALFORMED"}
        if manifest is not None and stored_manifest != manifest:
            return {"consistent": False, "code": "MANIFEST_MISMATCH"}
        for digest in stored_manifest.values():
            try:
                self.get_object(digest)
            except InputMissingError:
                return {"consistent": False, "code": "MANIFEST_OBJECT_MISSING"}
        return {"consistent": True, "code": "OK"}

    def verify_store_consistency(self, manifest: dict[str, str]) -> None:
        for digest in manifest.values():
            self.get_object(digest)
    def export_atomic(self, payload: bytes, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return hashlib.sha256(payload).hexdigest()
