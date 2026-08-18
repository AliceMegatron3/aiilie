"""Consistent backup/restore helpers for Ledger migration gates."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class LedgerBackupError(RuntimeError):
    pass


def _file_manifest(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": digest})
    return files


def create_backup(db_path: Path, cold_dir: Path, metadata_path: Path, backup_dir: Path) -> dict[str, Any]:
    """Create SQLite-native and cold-file manifests without deleting source data."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        raise LedgerBackupError(f"Ledger database does not exist: {db_path}")
    backup_db = backup_dir / db_path.name
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_db))
    try:
        source.backup(target)
        target.commit()
        check = target.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise LedgerBackupError(f"backup integrity check failed: {check}")
    finally:
        target.close()
        source.close()
    manifest = {
        "database": {"source": str(db_path), "backup": str(backup_db), "integrity_check": "ok"},
        "cold_cards": _file_manifest(Path(cold_dir)),
        "metadata": _file_manifest(Path(metadata_path).parent) if Path(metadata_path).exists() else [],
    }
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    manifest_path = backup_dir / "ledger_backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup_dir": str(backup_dir), "database": str(backup_db), "manifest": str(manifest_path), "manifest_sha256": manifest["manifest_sha256"]}


def verify_restore(backup_db: Path) -> dict[str, Any]:
    """Verify a restored SQLite copy before it can be used by migration."""
    connection = sqlite3.connect(str(backup_db))
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = str(connection.execute("PRAGMA foreign_key_check").fetchone() or "ok")
        counts: dict[str, int] = {}
        for table in ("cards", "ledger_documents", "ledger_passages", "ledger_claims", "ledger_outbox"):
            try:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                counts[table] = 0
        return {"integrity_check": integrity, "foreign_key_check": foreign_keys, "counts": counts, "ready": integrity == "ok" and foreign_keys == "ok"}
    finally:
        connection.close()
