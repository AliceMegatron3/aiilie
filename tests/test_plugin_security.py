"""插件清单、安装边界和索引计数的安全回归测试。"""
from __future__ import annotations

import json
import zipfile

import pytest

from core.config_manager import config_manager
from core.plugin_manager import PluginManager
from models.cards import InfoCard
from services.indexer import CardIndexer


@pytest.fixture
def plugin_manager(tmp_path):
    manager = PluginManager()
    manager.plugins_dir = tmp_path / "plugins"
    manager.plugins_dir.mkdir(parents=True)
    manager.staging_dir = manager.plugins_dir / ".staging"
    manager.staging_dir.mkdir()
    manager.audit_file = manager.plugins_dir / "trust_audit.jsonl"
    return manager


def _write_manifest(root, **overrides):
    manifest = {
        "id": "local.extractor",
        "namespace": "local",
        "kind": "extractor",
        "version": "1.0.0",
        "api_version": "1.0",
        "entrypoint": "trusted_plugin:run",
        "input_schema": {},
        "output_schema": {},
        "license": "MIT",
    }
    manifest.update(overrides)
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (root / "README.md").write_text("trusted metadata", encoding="utf-8")


def test_manifest_without_entrypoint_is_rejected(plugin_manager, tmp_path):
    source = tmp_path / "plugin"
    _write_manifest(source, entrypoint=None, external_command=None)

    result = plugin_manager.install_plugin(str(source))

    assert result["success"] is False
    assert "entrypoint" in result["message"]


def test_zip_slip_is_rejected_before_install(plugin_manager, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "no")

    result = plugin_manager.install_plugin(str(archive))

    assert result["success"] is False
    assert "路径" in result["message"]
    assert not (tmp_path / "escape.txt").exists()


def test_trust_store_approval_is_digest_bound(plugin_manager, tmp_path):
    source = tmp_path / "plugin"
    _write_manifest(source)
    result = plugin_manager.install_plugin(str(source))
    assert result["success"] is True
    record = plugin_manager.approve_plugin("local.extractor")
    assert plugin_manager.is_trusted("local.extractor") is True
    (plugin_manager.plugins_dir / "local.extractor" / "README.md").write_text("changed", encoding="utf-8")
    plugin_manager.loaded_plugins["local.extractor"]["artifact_digest"] = plugin_manager._tree_digest(plugin_manager.plugins_dir / "local.extractor")
    assert plugin_manager.is_trusted("local.extractor") is False
    assert record["artifact_digest"]


def test_valid_plugin_is_installed_but_not_enabled_without_trust(plugin_manager, tmp_path):
    source = tmp_path / "plugin"
    _write_manifest(source)

    result = plugin_manager.install_plugin(str(source))

    assert result["success"] is True
    assert result["trust_state"] == "UNTRUSTED"
    assert plugin_manager.loaded_plugins["local.extractor"]["trust_state"] == "UNTRUSTED"


def test_revoke_plugin_audit_e2e(plugin_manager, tmp_path):
    """Batch 6 E2E：批准→撤销全链路必须留下 append-only 可回放审计事件。

    覆盖：APPROVE 与 REVOKE 各落一条；撤销后 trust_store 清除且插件降为
    REVOKED；审计轨最新在前、可按插件过滤；日志为 JSONL 逐行可回放。
    """
    source = tmp_path / "plugin"
    _write_manifest(source)
    assert plugin_manager.install_plugin(str(source))["success"] is True

    # 1) 批准 → 信任生效，落一条 APPROVE 审计
    approve = plugin_manager.approve_plugin("local.extractor", operator="author")
    assert plugin_manager.is_trusted("local.extractor") is True
    assert approve["artifact_digest"]

    # 2) 撤销 → trust_store 清除，插件降为 REVOKED，落一条 REVOKE 审计
    revoke = plugin_manager.revoke_plugin("local.extractor", operator="security")
    assert revoke["revoked"] is True
    assert "local.extractor" not in plugin_manager._read_trust_store()
    assert plugin_manager.loaded_plugins["local.extractor"]["trust_state"] == "REVOKED"

    # 3) 审计轨可读：最新在前，按插件过滤，两条 action 齐全且含必要细节
    trail = plugin_manager.audit_trail("local.extractor")
    assert [ev["action"] for ev in trail] == ["REVOKE", "APPROVE"]
    for ev in trail:
        assert ev["plugin_id"] == "local.extractor"
        assert ev["ts"]
        assert "digest=" in ev["detail"]
    assert all(ev.get("ts") for ev in trail)
    assert trail[0]["ts"] >= trail[1]["ts"]
    revoke_ev = trail[0]
    assert revoke_ev["action"] == "REVOKE"
    assert revoke_ev["operator"] == "security"
    assert "digest=sha256:" in revoke_ev["detail"]

    # 4) 审计文件为 JSONL（逐行可回放，非覆盖写）
    lines = [
        line
        for line in plugin_manager.audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {e["action"] for e in parsed} == {"APPROVE", "REVOKE"}

    # 5) 重放：新实例读取同一文件的完整历史（撤销与批准都可复现）
    replay = PluginManager()
    replay.plugins_dir = plugin_manager.plugins_dir
    replay.staging_dir = plugin_manager.staging_dir
    replay.audit_file = plugin_manager.audit_file
    replay_trail = replay.audit_trail()
    ordered = [e["action"] for e in replay_trail]
    assert ordered == ["REVOKE", "APPROVE"] or ordered == ["APPROVE", "REVOKE"]
    assert replay_trail[0]["action"] in {"APPROVE", "REVOKE"}


def test_revoke_untrusted_plugin_still_audits(plugin_manager):
    """即使撤销一个从未批准过的插件，REVOKE 事件也应记录（带回滚语义可解释）。"""
    result = plugin_manager.revoke_plugin("local.never_approved", operator="admin")
    assert result["revoked"] is False
    trail = plugin_manager.audit_trail("local.never_approved")
    assert len(trail) == 1
    assert trail[0]["action"] == "REVOKE"
    assert trail[0]["operator"] == "admin"
    assert "previous_trust=False" in trail[0]["detail"]


def test_load_all_restart_recovery_reloads_manifest_not_execute(tmp_path):
    """Batch 6：重启恢复——新 PluginManager 对同一目录 load_all() 重新装载 manifest
    （不导入/执行第三方代码），供后续服务端信任复核。"""
    plugins_dir = tmp_path / "plugins"
    source = tmp_path / "src"
    _write_manifest(source, external_command=[])

    first = PluginManager()
    first.plugins_dir = plugins_dir
    first.plugins_dir.mkdir(parents=True)
    first.staging_dir = plugins_dir / ".staging"
    first.staging_dir.mkdir()
    assert first.install_plugin(str(source))["success"] is True

    # 模拟重启：全新实例、同一目录
    restarted = PluginManager()
    restarted.plugins_dir = plugins_dir
    restarted.staging_dir = plugins_dir / ".staging"
    restarted.load_all()
    assert "local.extractor" in restarted.loaded_plugins
    manifest = restarted.loaded_plugins["local.extractor"]
    assert manifest["id"] == "local.extractor"
    assert restarted.is_trusted("local.extractor") is False  # 重启后仍需信任复核


@pytest.mark.asyncio
async def test_count_search_cards_is_not_capped(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：本用例验证 legacy cards 表计数语义。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        for i in range(7):
            await indexer.save_card(
                InfoCard(
                    source_book_id="book",
                    content=f"same keyword {i}",
                    card_sub_type="note",
                )
            )
        assert await indexer.count_search_cards(keyword="same keyword") == 7
    finally:
        await indexer.close()
