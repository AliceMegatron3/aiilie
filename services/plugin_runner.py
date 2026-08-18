"""受控 JSON 插件 Runner。

默认拒绝未信任插件；仅执行 manifest 明确声明的 external_command。
不支持通过 entrypoint 在宿主进程内动态导入第三方代码。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from core.config_manager import config_manager


class PluginExecutionError(RuntimeError):
    pass


class JsonPluginRunner:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    async def run(
        self,
        plugin_id: str,
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        # 阶段A fail-closed：隔离 worker / 内存预算未就绪前，一律拒绝执行插件。
        if not config_manager.get_bool("plugins.execution_enabled", False):
            raise PluginExecutionError("插件执行已禁用（plugins.execution_enabled=false）")
        manifest = self.manager.loaded_plugins.get(plugin_id)
        if manifest is None:
            raise PluginExecutionError("插件不存在")
        # 阶段B：每次执行必须服务端复核信任——重新比对 digest / 签名 / 撤销状态，
        # 不信任进程内可能被篡改的 trust_state 字段。
        if not self.manager.is_trusted(plugin_id):
            raise PluginExecutionError(f"插件 {plugin_id} 未通过服务端信任复核（digest 已变、签名失效或已撤销）")
        command = manifest.get("external_command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise PluginExecutionError("受控 Runner 只允许 manifest 声明的 external_command")
        if manifest.get("entrypoint"):
            raise PluginExecutionError("不允许通过 entrypoint 在宿主进程内执行第三方代码")
        if any(token in ("shell", "cmd", "powershell", ".bat", ".cmd") for token in command[0].lower().split()):
            raise PluginExecutionError("不允许执行 shell/bat/cmd 插件")

        budget = manifest.get("resource_budget", {}) or {}
        max_input = int(budget.get("max_input_bytes", 1024 * 1024))
        max_output = int(budget.get("max_output_bytes", 256 * 1024))
        timeout = float(timeout_seconds or budget.get("timeout_seconds", 30))
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > max_input:
            raise PluginExecutionError("插件输入超过资源预算")

        executable = command[0]
        if executable == "python" or executable == "python.exe":
            executable = sys.executable
        elif not os.path.isabs(executable):
            raise PluginExecutionError("外部插件 executable 必须是绝对路径或当前 Python 解释器")
        argv = [executable, *command[1:]]
        plugin_dir = Path(self.manager.plugins_dir) / plugin_id
        if not plugin_dir.is_dir():
            raise PluginExecutionError("插件工作目录不存在，插件可能尚未完成安装")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PLUGIN_ID": plugin_id,
        }
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(plugin_dir),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=False,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(encoded), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise PluginExecutionError("插件执行超时，进程已终止") from exc
        if len(stdout) > max_output:
            raise PluginExecutionError("插件输出超过资源预算")
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[:2000]
            raise PluginExecutionError(f"插件执行失败({process.returncode}): {detail}")
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginExecutionError("插件必须返回合法 JSON") from exc
        if not isinstance(result, dict):
            raise PluginExecutionError("插件输出必须是 JSON 对象")
        return result
