"""Ledger backup/restore verification tests."""
from __future__ import annotations

import sqlite3

from services.ledger_backup import create_backup, verify_restore


def test_sqlite_backup_and_restore_integrity(tmp_path):
    source_db = tmp_path / "library_index.db"
    connection = sqlite3.connect(source_db)
    connection.execute("CREATE TABLE cards(card_id TEXT PRIMARY KEY, content TEXT NOT NULL)")
    connection.execute("INSERT INTO cards VALUES ('card-1', 'content')")
    connection.commit()
    connection.close()
    cold = tmp_path / "cards"
    cold.mkdir()
    (cold / "card-1.json").write_text('{"card_id":"card-1"}', encoding="utf-8")
    metadata = tmp_path / "books_metadata.json"
    metadata.write_text("{}", encoding="utf-8")

    result = create_backup(source_db, cold, metadata, tmp_path / "backup")
    restored = verify_restore(tmp_path / "backup" / "library_index.db")

    assert result["manifest_sha256"]
    assert restored["ready"] is True
    assert restored["integrity_check"] == "ok"
    assert restored["counts"]["cards"] == 1
