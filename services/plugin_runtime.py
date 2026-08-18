"""可扩展插件内核 — PluginContext/Manifest 策略接入 PluginManager 生命周期（V0.5 收口）。

把已建构件（`PluginContext` 受限访问面 + `validate_manifest` 入站清单）接进
插件运行时生命周期，把「声明即限定」落到 enable 门：

- **清单策略门**：启用前跑 `validate_manifest`（fail-closed，越权声明即拒）；
- **信任/撤销门**：`is_trusted`（digest+signer+cosign）不过或已撤销 → 不产生
  context、不启用（撤销后 enable 必然被拒，fail-closed）；
- **provider 类型映射**：`kind → provider_type`（knowledge/resource/processor），
  未知 kind 拒绝启用；
- **能力白名单裁剪**：只按 manifest 声明的 capabilities 注入对应接线，
  未声明的能力在 context 上不可用（声明即限定）；
- **生命周期**：全门通过 → ENABLED→RUNNING（context 就绪）；任一不过 → ERROR；
  撤销/停用 → DISABLED 并清空 context；
- **外部进程隔离**：`run_external_isolated` 无 shell（list 形式）+ 超时 + 输出上限；
- **WASI**：当前无 WASM 沙箱运行时 → 结构化 `DISABLED`（manifest 声明 wasi 运行时
  即拒，不伪装已隔离）。

验证：`tests/test_plugin_runtime.py`。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from core.plugin_lifecycle import PluginState, plugin_lifecycle
from services.plugin_context import ContextDeniedError, PluginContext, ResourceBudget
from services.plugin_manifest_policy import ManifestPolicyError, validate_manifest

# kind → provider_type 映射（provider 类型映射）
PROVIDER_TYPE_BY_KIND: dict[str, str] = {
    "skill": "knowledge",
    "projection": "knowledge",
    "law": "knowledge",
    "resource": "resource",
    "extractor": "processor",
    "metric": "processor",
    "validator": "processor",
    "writer": "processor",
}

# context 方法名 ↔ 接线参数名（能力白名单）
_CONTEXT_CAPABILITY_WIRING = {
    "read_document": "read_doc",
    "search_resources": "search",
    "submit_candidate": "submit_candidate",
    "emit_audit_event": "audit",
}

WASI_RUNTIME = "wasi"


class PluginRuntimeError(RuntimeError):
    pass


# 运行时 context 注册表（plugin_id -> PluginContext）
_runtime_contexts: dict[str, PluginContext] = {}


def map_provider_type(manifest: dict[str, Any]) -> str | None:
    """kind → provider_type 映射；未知 kind 返回 None（fail-closed）。"""
    kind = str(manifest.get("kind", "") or "")
    return PROVIDER_TYPE_BY_KIND.get(kind)


def build_plugin_context(
    plugin_id: str,
    policy: dict[str, Any],
    *,
    project_scope: str | None = None,
    wiring: dict[str, Any] | None = None,
) -> PluginContext:
    """由规范化策略构造受限访问面；仅注入插件声明的 capabilities 对应接线。

    `wiring` 为宿主提供的真实接线（read_doc/search/submit_candidate/audit）；
    未声明或未提供的能力一律不注入（context 上不可用，fail-closed）。
    """
    budget = policy["resource_budget"]
    caps = set(policy["capabilities"])
    wiring = wiring or {}
    kwargs: dict[str, Any] = {}
    for cap, key in _CONTEXT_CAPABILITY_WIRING.items():
        if cap in caps and wiring.get(key) is not None:
            kwargs[key] = wiring[key]
    return PluginContext(
        identity=plugin_id,
        project_scope=project_scope,
        budget=ResourceBudget(
            timeout_seconds=int(budget["timeout_seconds"]),
            max_input_bytes=int(budget["max_input_bytes"]),
            max_output_bytes=int(budget["max_output_bytes"]),
        ),
        **kwargs,
    )


def _transition(plugin_id: str, target: PluginState, error_message: str | None = None) -> None:
    """尽力推进生命周期状态（非法/重复转移安全跳过，门控不依赖状态机）。"""
    record = plugin_lifecycle.get(plugin_id)
    if record is None:
        return
    try:
        record.transition(target, error_message=error_message)
    except ValueError:
        pass


def enable_plugin(
    manager: Any,
    plugin_id: str,
    *,
    project_scope: str | None = None,
    wiring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 PluginContext/validate_manifest 接进生命周期：全门通过才启用并产出 context。

    门（顺序执行，任一不过 → ERROR + 不产生 context，fail-closed）：
      存在 → 信任/撤销门 → 清单策略门 → WASI 检查 → provider 类型映射。
    """
    manifest = manager.loaded_plugins.get(plugin_id)
    if manifest is None:
        raise PluginRuntimeError(f"插件不存在或尚未加载: {plugin_id}")
    # 任何 enable 尝试先清空既有 context（撤销/拒绝后不得残留运行时访问）
    _runtime_contexts.pop(plugin_id, None)

    # 信任/撤销门：未信任（含已撤销）→ 拒绝
    if not manager.is_trusted(plugin_id):
        _transition(plugin_id, PluginState.ERROR, "插件未通过信任校验，禁止启用")
        return {"plugin_id": plugin_id, "enabled": False, "reason": "untrusted"}

    # 清单策略门
    try:
        policy = validate_manifest(manifest)
    except ManifestPolicyError as exc:
        _transition(plugin_id, PluginState.ERROR, f"manifest 策略拒绝: {exc}")
        return {
            "plugin_id": plugin_id, "enabled": False,
            "reason": "manifest_policy_rejected", "detail": str(exc),
        }

    # WASI 沙箱运行时未接入 → 结构化 DISABLED（不伪装已隔离）
    if str(manifest.get("runtime", "") or "").lower() == WASI_RUNTIME:
        _transition(plugin_id, PluginState.ERROR, "WASI 沙箱运行时未接入，禁止启用")
        return {
            "plugin_id": plugin_id, "enabled": False, "status": "DISABLED",
            "reason": "wasi_runtime_unavailable",
        }

    # provider 类型映射
    provider_type = map_provider_type(manifest)
    if not provider_type:
        _transition(plugin_id, PluginState.ERROR, f"未知插件 kind: {manifest.get('kind')}")
        return {"plugin_id": plugin_id, "enabled": False, "reason": "unknown_provider_type"}

    # 构建受限访问面并按声明能力裁剪接线
    context = build_plugin_context(
        plugin_id, policy, project_scope=project_scope, wiring=wiring
    )
    _runtime_contexts[plugin_id] = context
    _transition(plugin_id, PluginState.ENABLED)
    _transition(plugin_id, PluginState.RUNNING)
    return {
        "plugin_id": plugin_id,
        "enabled": True,
        "provider_type": provider_type,
        "state": PluginState.RUNNING.value,
        "budget": policy["resource_budget"],
    }


def disable_plugin(manager: Any, plugin_id: str, *, reason: str = "disabled") -> dict[str, Any]:
    """停用插件：清空 context 并置 DISABLED（撤销后调用以切断运行时访问）。"""
    _runtime_contexts.pop(plugin_id, None)
    _transition(plugin_id, PluginState.DISABLED, reason)
    return {"plugin_id": plugin_id, "enabled": False, "state": PluginState.DISABLED.value, "reason": reason}


def get_runtime_context(plugin_id: str) -> PluginContext | None:
    """取已启用插件的受限访问面（未启用/被拒/已撤销 → None）。"""
    return _runtime_contexts.get(plugin_id)


# ── 外部进程隔离（无 shell / 超时 / 输出上限） ─────────────────────────────

async def run_external_isolated(
    command: list[str],
    *,
    timeout_seconds: int = 30,
    max_output_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """隔离执行外部命令（插件 external_command）。

    安全不变量：
    - `command` 必须是字符串列表（无 shell，杜绝 shell 注入）；
    - 超时强制杀掉并返回 TIMEOUT；
    - stdout/stderr 按字节上限截断，返回是否截断标记（防输出炸弹）。
    """
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise PluginRuntimeError("external_command 必须是字符串列表（禁止 shell 字符串）")
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=max(1, int(timeout_seconds))
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {"status": "TIMEOUT", "timeout_seconds": int(timeout_seconds)}
    except FileNotFoundError:
        return {"status": "ERROR", "error": "executable_not_found"}

    stdout_text = stdout[:max_output_bytes].decode("utf-8", errors="replace")
    stderr_text = stderr[:max_output_bytes].decode("utf-8", errors="replace")
    return {
        "status": "OK" if proc.returncode == 0 else "ERROR",
        "returncode": proc.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "stdout_truncated": len(stdout) > max_output_bytes,
        "stderr_truncated": len(stderr) > max_output_bytes,
    }


__all__ = [
    "ContextDeniedError",
    "PluginRuntimeError",
    "build_plugin_context",
    "disable_plugin",
    "enable_plugin",
    "get_runtime_context",
    "map_provider_type",
    "run_external_isolated",
]
