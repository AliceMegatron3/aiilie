"""审核接口与插件规划器的真实 API 回归测试。"""
from __future__ import annotations

import json

import httpx
import pytest

from api.deps import get_db, verify_token
from core.config_manager import config_manager
from core.database import DatabaseManager
from core.plugin_manager import PluginManager
from core.plugin_manager import plugin_manager as _pm
from main import create_app


@pytest.mark.asyncio
async def test_rule_approval_reads_and_commits_real_database(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：本用例属于 governance/reflection 规则审批回归，与 ledger
    # 无关。authoritative=true 时 bootstrap 会对临时 library_index.db 执行 readiness
    # 门禁（tmp 库未跑迁移 → RuntimeError），此处临时关闭 authoritative 避免误伤；
    # monkeypatch 在用例结束后自动还原配置。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
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
                assert pending.json()["data"]["data"][0]["rule_id"] == "rule-real"

                approved = await client.post("/api/v1/reflection/rules/rule-real/approve")
                assert approved.status_code == 200
                assert approved.json()["data"]["status"] == "success"

                repeated = await client.post("/api/v1/reflection/rules/rule-real/approve")
                assert repeated.status_code == 200
                assert repeated.json()["data"]["status"] == "already_active"

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


def _write_audit_plugin(root, plugin_id="local.auditee"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({
            "id": plugin_id,
            "namespace": "local",
            "kind": "extractor",
            "version": "1.0.0",
            "api_version": "1.0",
            "entrypoint": "trusted_plugin:run",
            "input_schema": {},
            "output_schema": {},
            "license": "MIT",
        }),
        encoding="utf-8",
    )
    (root / "README.md").write_text("audit e2e", encoding="utf-8")


@pytest.mark.asyncio
async def test_plugin_revoke_audit_e2e_via_api(tmp_path, monkeypatch):
    """Batch 6 栈级 E2E：安装→批准→撤销→读审计轨，全程经 HTTP。

    隔离全局 plugin_manager 的目录到临时路径，避免污染真实应用数据目录；
    覆盖 /plugins/{id}/audit 与 /plugins/audit 读取语义（最新在前、可过滤）。
    """
    plugin_id = "local.auditee"
    source = tmp_path / "src"
    _write_audit_plugin(source)

    # 把全局单例的存储路径隔离到临时目录
    monkeypatch.setattr(_pm, "plugins_dir", tmp_path / "plugins")
    monkeypatch.setattr(_pm, "staging_dir", _pm.plugins_dir / ".staging")
    monkeypatch.setattr(_pm, "audit_file", _pm.plugins_dir / "trust_audit.jsonl")
    _pm.plugins_dir.mkdir(parents=True, exist_ok=True)
    _pm.staging_dir.mkdir(parents=True)
    _pm.loaded_plugins.clear()

    db = DatabaseManager(db_path=tmp_path / "audit.db")
    await db.initialize()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 安装
                install = await client.post(
                    "/api/v1/plugins/install",
                    json={"source_path": str(source)},
                )
                assert install.status_code == 200
                assert install.json()["success"] is True

                # 批准 → 信任生效
                approve = await client.post(
                    f"/api/v1/plugins/{plugin_id}/approve",
                    json={"operator": "author"},
                )
                assert approve.status_code == 200
                assert approve.json()["data"]["plugin_id"] == plugin_id

                # 撤销 → 拒绝再启用（如果可见）
                revoke = await client.post(
                    f"/api/v1/plugins/{plugin_id}/revoke",
                    json={"operator": "security"},
                )
                assert revoke.status_code == 200
                assert revoke.json()["data"]["revoked"] is True

                # 按插件读审计轨：最新在前 [REVOKE, APPROVE]
                audit = await client.get(f"/api/v1/plugins/{plugin_id}/audit")
                assert audit.status_code == 200
                events = audit.json()["data"]
                actions = [e["action"] for e in events]
                assert actions == ["REVOKE", "APPROVE"]
                assert events[0]["operator"] == "security"
                assert events[1]["operator"] == "author"
                assert all(e["plugin_id"] == plugin_id for e in events)

                # 全局审计轨也包含该两条，且同一 REVOKE 在 APPROVE 之前
                all_events = (await client.get("/api/v1/plugins/audit")).json()["data"]
                filtered = [e for e in all_events if e["plugin_id"] == plugin_id]
                assert [e["action"] for e in filtered] == ["REVOKE", "APPROVE"]

                # limit 越界 fail-closed
                bad = await client.get(f"/api/v1/plugins/{plugin_id}/audit?limit=0")
                assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await db.close()


@pytest.mark.asyncio
async def test_auth_required_rejects_anonymous_and_allows_token(tmp_path, monkeypatch):
    """P3/A8：开启 security.require_auth 后，未带 token 的 API 请求被 401 拒绝，
    携带有效 token 通过——鉴权一致、fail-closed。

    说明：api.deps.verify_token 是模块 import 期用 env 快照构造的闭包；测试直接
    用 create_auth_dependency() 重建并覆写依赖，验证真实鉴权闭包行为。"""
    from core.security import create_auth_dependency, generate_api_token

    monkeypatch.setenv("AIILIE_SECURITY_REQUIRE_AUTH", "1")
    monkeypatch.setenv("AIILIE_SECURITY_AUTH_SECRET", "p3-test-secret")
    config_manager.reload()

    # 重建鉴权闭包（读取当前 env），覆写全局依赖
    auth_dep = create_auth_dependency()

    db = DatabaseManager(db_path=tmp_path / "auth.db")
    await db.initialize()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_token] = auth_dep
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                # 无 token → 401（fail-closed）
                anon = await client.get("/api/v1/plugins")
                assert anon.status_code == 401

                # 伪造/无效 token → 401
                bad = await client.get(
                    "/api/v1/plugins", headers={"X-API-Token": "forged.token.xxx"}
                )
                assert bad.status_code == 401

                # 有效 token（服务端签发）→ 200
                token = generate_api_token("p3-client")
                ok = await client.get(
                    "/api/v1/plugins", headers={"X-API-Token": token}
                )
                assert ok.status_code == 200
                assert "data" in ok.json()  # /plugins 裸包络 {data:[...]}
    finally:
        app.dependency_overrides.clear()
        await db.close()