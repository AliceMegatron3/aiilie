"""PluginContext/Manifest 策略接入 PluginManager 生命周期测试（V0.5 收口）。

覆盖：
- enable 门：信任/撤销门、manifest 策略门、WASI 无运行时拒、provider 类型映射；
- 全门通过才构建 PluginContext（identity/scope/budget/能力白名单裁剪）；
- 撤销后 enable 必被拒且不残留 context（fail-closed）；
- 外部进程隔离：无 shell / 超时 / 输出上限 / 可执行缺失；
- provider 类型映射确定性。
"""
from __future__ import annotations

import json
import sys

import pytest

from core.plugin_manager import PluginManager
from services.plugin_context import ContextDeniedError, PluginContext
from services.plugin_runtime import (
    PluginRuntimeError,
    disable_plugin,
    enable_plugin,
    get_runtime_context,
    map_provider_type,
    run_external_isolated,
)


def _write_manifest(root, plugin_id: str, **overrides):
    manifest = {
        "id": plugin_id,
        "namespace": "local",
        "kind": "extractor",
        "version": "1.0.0",
        "api_version": "1.0",
        "entrypoint": "trusted_plugin:run",
        "input_schema": {},
        "output_schema": {},
        "capabilities": ["read_document", "search_resources", "emit_audit_event"],
        "license": "MIT",
    }
    manifest.update(overrides)
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (root / "README.md").write_text("metadata", encoding="utf-8")


@pytest.fixture
def manager(tmp_path) -> PluginManager:
    m = PluginManager()
    m.plugins_dir = tmp_path / "plugins"
    m.plugins_dir.mkdir(parents=True)
    m.staging_dir = m.plugins_dir / ".staging"
    m.staging_dir.mkdir()
    return m


def _install(manager: PluginManager, tmp_path, plugin_id: str, **overrides) -> str:
    _write_manifest(tmp_path / "plugin", plugin_id, **overrides)
    result = manager.install_plugin(str(tmp_path / "plugin"))
    assert result["success"] is True, result
    return plugin_id


def _install_and_approve(manager: PluginManager, tmp_path, plugin_id: str, **overrides) -> str:
    _install(manager, tmp_path, plugin_id, **overrides)
    manager.approve_plugin(plugin_id)
    assert manager.is_trusted(plugin_id) is True
    return plugin_id


async def _fake_reader(project_id: str, doc_id: str):
    return {"project_id": project_id, "doc_id": doc_id}


# ── 生命周期接入 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enable_plugin_builds_context(manager, tmp_path):
    pid = _install_and_approve(manager, tmp_path, "local.runtime_ok")
    wiring = {"read_doc": _fake_reader, "audit": lambda event: None}
    outcome = enable_plugin(manager, pid, project_scope="p1", wiring=wiring)
    assert outcome["enabled"] is True
    assert outcome["provider_type"] == "processor"
    assert outcome["state"] == "RUNNING"

    ctx = get_runtime_context(pid)
    assert isinstance(ctx, PluginContext)
    assert ctx.identity == pid
    assert ctx.project_scope == "p1"
    # 声明且注入的能力可用
    assert (await ctx.read_document("p1", "d1"))["doc_id"] == "d1"
    # 声明但未注入接线 → ContextDeniedError（fail-closed）
    with pytest.raises(ContextDeniedError, match="search_resources"):
        await ctx.search_resources("x", 5)


def test_untrusted_prevents_enable(manager, tmp_path):
    pid = _install(manager, tmp_path, "local.runtime_untrusted")  # 未批准
    outcome = enable_plugin(manager, pid)
    assert outcome["enabled"] is False
    assert outcome["reason"] == "untrusted"
    assert get_runtime_context(pid) is None


def test_revoked_prevents_enable_and_clears_context(manager, tmp_path):
    pid = _install_and_approve(manager, tmp_path, "local.runtime_revoke")
    assert enable_plugin(manager, pid, project_scope="p1")["enabled"] is True
    assert get_runtime_context(pid) is not None
    # 撤销后重新 enable 必被拒，且不残留运行时 context
    manager.revoke_plugin(pid, operator="author")
    outcome = enable_plugin(manager, pid)
    assert outcome["enabled"] is False
    assert outcome["reason"] == "untrusted"
    assert get_runtime_context(pid) is None


def test_manifest_policy_rejected_prevents_enable(manager):
    # 直接注入带越权权限的 manifest（PluginManager 的 PluginManifest 禁止 extra 字段，
    # 此处模拟运行时清单里出现的高危声明，enable 门须 fail-closed）
    manager.loaded_plugins["local.runtime_evil"] = {
        "id": "local.runtime_evil",
        "kind": "extractor",
        "api_version": "1.0",
        "capabilities": ["read_document"],
        "permissions": ["ledger.write"],
        "artifact_digest": "sha256:deadbeef",
    }
    manager.approve_plugin("local.runtime_evil")
    outcome = enable_plugin(manager, "local.runtime_evil")
    assert outcome["enabled"] is False
    assert outcome["reason"] == "manifest_policy_rejected"
    assert "ledger" in outcome["detail"]
    assert get_runtime_context("local.runtime_evil") is None


def test_wasi_runtime_disabled(manager):
    # PluginManager 的 PluginManifest 禁止 extra 字段，此处模拟运行时清单声明 wasi
    # 运行时（无 WASM 沙箱）→ enable 必须返回结构化 DISABLED（不伪装已隔离）
    manager.loaded_plugins["local.runtime_wasi"] = {
        "id": "local.runtime_wasi",
        "kind": "extractor",
        "api_version": "1.0",
        "capabilities": ["read_document"],
        "runtime": "wasi",
        "artifact_digest": "sha256:wasi",
    }
    manager.approve_plugin("local.runtime_wasi")
    outcome = enable_plugin(manager, "local.runtime_wasi")
    assert outcome["enabled"] is False
    assert outcome["status"] == "DISABLED"
    assert outcome["reason"] == "wasi_runtime_unavailable"
    assert get_runtime_context("local.runtime_wasi") is None


def test_disable_plugin_clears_context(manager, tmp_path):
    pid = _install_and_approve(manager, tmp_path, "local.runtime_stop")
    assert enable_plugin(manager, pid)["enabled"] is True
    assert get_runtime_context(pid) is not None
    disable_plugin(manager, pid, reason="stopped")
    assert get_runtime_context(pid) is None


def test_unknown_plugin_raises(manager):
    with pytest.raises(PluginRuntimeError, match="不存在"):
        enable_plugin(manager, "local.missing")


# ── provider 类型映射 ─────────────────────────────────────────


def test_map_provider_type_deterministic():
    assert map_provider_type({"kind": "skill"}) == "knowledge"
    assert map_provider_type({"kind": "projection"}) == "knowledge"
    assert map_provider_type({"kind": "resource"}) == "resource"
    assert map_provider_type({"kind": "extractor"}) == "processor"
    assert map_provider_type({"kind": "writer"}) == "processor"
    assert map_provider_type({"kind": "hacker"}) is None  # 未知 kind → fail-closed
    assert map_provider_type({}) is None


# ── 外部进程隔离 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_external_isolated_success():
    result = await run_external_isolated([sys.executable, "-c", "print('hello')"])
    assert result["status"] == "OK"
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]
    assert result["stdout_truncated"] is False


@pytest.mark.asyncio
async def test_run_external_isolated_timeout():
    result = await run_external_isolated(
        [sys.executable, "-c", "import time; time.sleep(10)"], timeout_seconds=1
    )
    assert result["status"] == "TIMEOUT"


@pytest.mark.asyncio
async def test_run_external_isolated_rejects_shell_string():
    with pytest.raises(PluginRuntimeError, match="字符串列表"):
        await run_external_isolated("echo hello")


@pytest.mark.asyncio
async def test_run_external_isolated_executable_missing():
    result = await run_external_isolated(["definitely_not_a_real_exe_xyz"])
    assert result["status"] == "ERROR"
    assert result["error"] == "executable_not_found"


@pytest.mark.asyncio
async def test_run_external_isolated_output_capped():
    result = await run_external_isolated(
        [sys.executable, "-c", "print('x' * 5000)"], max_output_bytes=64
    )
    assert result["status"] == "OK"
    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) <= 64
