"""部署加固：信任报告、依赖锁定和迁移配置测试。"""
from __future__ import annotations

import json

from core.plugin_manager import PluginManager


def test_trust_report_exposes_digest_and_signer_policy(tmp_path):
    manager = PluginManager()
    manager.plugins_dir = tmp_path / "plugins"
    manager.plugins_dir.mkdir()
    manager.staging_dir = manager.plugins_dir / ".staging"
    manager.staging_dir.mkdir()
    manager.trust_file = manager.plugins_dir / "trust_store.json"
    plugin_dir = manager.plugins_dir / "signed.plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{}", encoding="utf-8")
    digest = "sha256:abc"
    manager.loaded_plugins["signed.plugin"] = {
        "id": "signed.plugin", "artifact_digest": digest, "signer": "sigstore:alice",
        "trust_state": "UNTRUSTED",
    }
    report = manager.trust_report("signed.plugin")
    assert report["trusted"] is False
    assert report["digest_match"] is False


def test_trust_policy_requires_allowlist_when_signer_required(tmp_path):
    manager = PluginManager()
    manager.plugins_dir = tmp_path / "plugins"
    manager.plugins_dir.mkdir()
    manager.staging_dir = manager.plugins_dir / ".staging"
    manager.staging_dir.mkdir()
    manager.policy_file = manager.plugins_dir / "trust_policy.json"
    try:
        manager.set_trust_policy([], require_signer=True)
    except ValueError:
        pass
    else:
        raise AssertionError("signer 策略缺少 allowlist 时应拒绝")
