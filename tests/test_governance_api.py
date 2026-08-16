"""审核接口与插件规划器的真实 API 回归测试。"""
from __future__ import annotations

import httpx
import pytest

from api.deps import get_db
from core.database import DatabaseManager
from core.plugin_manager import PluginManager
from main import create_app


@pytest.mark.asyncio
async def test_rule_approval_reads_and_commits_real_database(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "governance.db")
    await db.initialize()
    await db.conn.execute(
        """CREATE TABLE IF NOT EXISTS optimization_rules (
            rule_id TEXT PRIMARY KEY, scope TEXT NOT NULL, condition TEXT NOT NULL,
            action TEXT NOT NULL, confidence REAL NOT NULL, is_active INTEGER DEFAULT 1,
            created_at REAL NOT NULL, feedback_score REAL DEFAULT 0.0
        )"""
    )
    await db.conn.execute(
        """INSERT INTO optimization_rules
        (rule_id, scope, condition, action, confidence, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("rule-real", "book", "{}", "{}", 0.8, 0, 1.0),
    )
    await db.conn.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                pending = await client.get("/api/v1/reflection/pending-rules")
                assert pending.status_code == 200
                assert pending.json()["data"][0]["rule_id"] == "rule-real"

                approved = await client.post("/api/v1/reflection/rules/rule-real/approve")
                assert approved.status_code == 200
                assert approved.json()["status"] == "success"

                repeated = await client.post("/api/v1/reflection/rules/rule-real/approve")
                assert repeated.status_code == 200
                assert repeated.json()["status"] == "already_active"

                missing = await client.post("/api/v1/reflection/rules/not-found/approve")
                assert missing.status_code == 404

        cursor = await db.conn.execute(
            "SELECT is_active FROM optimization_rules WHERE rule_id = ?", ("rule-real",)
        )
        assert (await cursor.fetchone())[0] == 1
    finally:
        app.dependency_overrides.clear()
        await db.close()


def test_plugin_plan_rejects_invalid_model_plan_and_allows_empty_schema():
    manager = PluginManager()
    manager.loaded_plugins = {
        "empty-schema": {
            "id": "empty-schema",
            "version": "1.0.0",
            "permission_level": "L2",
            "input_schema": {},
            "output_schema": {},
        }
    }
    context = {"plugin_id": "empty-schema", "caller_permission": 2}

    allowed = manager.deterministic_call_plan(context)
    assert allowed["decision"] == "ALLOW"

    forged = manager.validate_call_plan(
        context,
        {
            "decision": "ALLOW",
            "calls": [{"plugin_id": "other-plugin", "permission_level": "L6"}],
        },
    )
    assert forged["decision"] == "DENY"
    assert forged["calls"] == []

    malformed = PluginManager()
    malformed.loaded_plugins = {
        "bad": {
            "id": "bad",
            "permission_level": "L9",
            "input_schema": {},
            "output_schema": {},
        }
    }
    assert malformed.deterministic_call_plan(
        {"plugin_id": "bad", "caller_permission": 6}
    )["decision"] == "DENY"