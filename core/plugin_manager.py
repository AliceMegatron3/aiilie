import os
import json
import logging
from typing import Any
from pathlib import Path
from utils.resource_path import get_resource_path
# 注意：services.prompt_template_manager 的引用已移入 render_call_plan_prompt 函数体内
# 以消除 core→services 的模块级反向依赖（讨论稿20260816第二章差距盘点#6）。

logger = logging.getLogger(__name__)

class PluginManager:
    """
    补丁C扩展：轻量级插件系统。
    允许动态加载第三方扩展，而无需修改系统源码。
    """
    def __init__(self):
        self.plugins_dir = get_resource_path("plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.loaded_plugins = {}

    def load_all(self):
        logger.info(f"正在扫描插件目录: {self.plugins_dir}")
        for plugin_path in self.plugins_dir.iterdir():
            if plugin_path.is_dir():
                manifest_file = plugin_path / "plugin.json"
                if manifest_file.exists():
                    self._load_plugin(manifest_file)

    def _load_plugin(self, manifest_file: Path):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            
            plugin_id = manifest.get("id")
            logger.info(f"🧩 发现插件: {manifest.get('name')} (v{manifest.get('version')})")
            
            # 此处可以添加沙盒隔离机制（如 restrictedpython）
            # 当前演示：将其记录到已加载列表
            self.loaded_plugins[plugin_id] = manifest
            
            # 假设插件提供了新的提取策略
            strategies = manifest.get("extraction_strategies", [])
            for strategy in strategies:
                logger.info(f"   -> 注册扩展策略: {strategy}")
                
        except Exception as e:
            logger.error(f"加载插件失败 {manifest_file}: {e}")

    def get_manifests(self) -> list[dict[str, Any]]:
        """返回规划器可见的插件清单副本。"""
        return [dict(manifest) for manifest in self.loaded_plugins.values()]

    def render_call_plan_prompt(self, context: dict[str, Any]) -> str:
        """仅生成调用规划提示词；此方法不执行插件，也不改变插件状态。"""
        import json

        variables = {
            "caller_id": context.get("caller_id", "unknown"),
            "caller_permission": context.get("caller_permission", 0),
            "task_id": context.get("task_id", ""),
            "project_id": context.get("project_id", ""),
            "book_id": context.get("book_id", ""),
            "task_description": context.get("task_description", ""),
            "plugin_manifests": json.dumps(self.get_manifests(), ensure_ascii=False),
            "available_assets": json.dumps(context.get("available_assets", []), ensure_ascii=False),
        }
        # 延迟 import：core 层不在模块加载时依赖 services 层
        from services.prompt_template_manager import prompt_manager

        return prompt_manager.render("plugin_call_planner", variables)

    def deterministic_call_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        """无模型时的安全规划器：缺少插件、权限或 schema 信息时拒绝调用。"""
        try:
            caller_permission = int(context.get("caller_permission", 0) or 0)
        except (TypeError, ValueError):
            return self._deny_plan(
                str(context.get("plugin_id", "") or ""),
                "调用者权限等级非法",
                "invalid_permission",
            )
        if caller_permission < 0 or caller_permission > 6:
            return self._deny_plan(
                str(context.get("plugin_id", "") or ""),
                "调用者权限等级越界",
                "invalid_permission",
            )
        plugin_id = context.get("plugin_id", "")
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return {
                "decision": "DENY",
                "reason": "插件不存在或尚未加载",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "not_loaded"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        required = manifest.get("permission_level", manifest.get("permissions_required", 0))
        try:
            if isinstance(required, str):
                required = int(required.removeprefix("L") or 0)
            required = int(required or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "插件权限等级非法", "invalid_permission")
        if required < 0 or required > 6:
            return self._deny_plan(plugin_id, "插件权限等级越界", "invalid_permission")
        if required > caller_permission:
            return {
                "decision": "DENY",
                "reason": "调用者权限不足",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "permission_denied"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        # 空对象是合法的 JSON Schema（表示无参数/空输出），只有缺少字段才拒绝。
        if (
            "input_schema" not in manifest
            or "output_schema" not in manifest
            or not isinstance(manifest["input_schema"], dict)
            or not isinstance(manifest["output_schema"], dict)
        ):
            return {
                "decision": "DENY",
                "reason": "插件 manifest 缺少 input_schema 或 output_schema",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "schema_missing"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        return {
            "decision": "ALLOW",
            "reason": "仅生成待执行计划，尚未执行插件",
            "calls": [{
                "plugin_id": plugin_id,
                "plugin_version": manifest.get("version", ""),
                "permission_level": f"L{caller_permission}",
                "timeout_seconds": 60,
                "max_retries": 1,
                "side_effects": manifest.get("side_effects", []),
                "checkpoint": True,
                "result_status": "PLANNED",
            }],
            "rejected_calls": [],
            "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 60, "max_memory_mb": 0},
        }

    def validate_call_plan(self, context: dict[str, Any], plan: Any) -> dict[str, Any]:
        """校验模型提出的计划，禁止模型绕过插件、权限或 schema 闸门。

        该方法只返回可审计的计划，不执行插件。任何结构、权限或插件身份
        不一致都降级为 DENY，避免 API 将模型的任意 JSON 当成成功规划。
        """
        plugin_id = str(context.get("plugin_id", "") or "")
        try:
            caller_permission = int(context.get("caller_permission", 0) or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "调用者权限等级非法", "invalid_permission")
        if caller_permission < 0 or caller_permission > 6:
            return self._deny_plan(plugin_id, "调用者权限等级越界", "invalid_permission")
        if not isinstance(plan, dict) or plan.get("decision") != "ALLOW":
            if isinstance(plan, dict) and plan.get("decision") == "DENY":
                return self._deny_plan(
                    plugin_id,
                    str(plan.get("reason") or "插件规划拒绝调用"),
                    "planner_denied",
                )
            return self._deny_plan(plugin_id, "插件规划结果未明确允许调用", "invalid_decision")

        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return self._deny_plan(plugin_id, "插件不存在或尚未加载", "not_loaded")

        required = manifest.get("permission_level", manifest.get("permissions_required", 0))
        try:
            if isinstance(required, str):
                required = int(required.removeprefix("L") or 0)
            required = int(required or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "插件权限等级非法", "invalid_permission")
        if required < 0 or required > 6:
            return self._deny_plan(plugin_id, "插件权限等级越界", "invalid_permission")
        if required > caller_permission:
            return self._deny_plan(plugin_id, "调用者权限不足", "permission_denied")
        if (
            "input_schema" not in manifest
            or "output_schema" not in manifest
            or not isinstance(manifest["input_schema"], dict)
            or not isinstance(manifest["output_schema"], dict)
        ):
            return self._deny_plan(plugin_id, "插件 manifest 缺少 input_schema 或 output_schema", "schema_missing")

        calls = plan.get("calls")
        if not isinstance(calls, list) or not calls:
            return self._deny_plan(plugin_id, "插件规划缺少待执行调用", "calls_missing")
        for call in calls:
            if not isinstance(call, dict) or call.get("plugin_id") != plugin_id:
                return self._deny_plan(plugin_id, "规划调用的插件身份不一致", "plugin_mismatch")
            try:
                declared_permission = call.get("permission_level", f"L{caller_permission}")
                if isinstance(declared_permission, str):
                    declared_permission = int(declared_permission.removeprefix("L") or 0)
                if int(declared_permission) > caller_permission or int(declared_permission) < required:
                    return self._deny_plan(plugin_id, "规划调用权限越界", "permission_escalation")
            except (TypeError, ValueError):
                return self._deny_plan(plugin_id, "规划调用权限等级非法", "invalid_call_permission")

        plan["decision"] = "ALLOW"
        plan["status"] = "PLANNED"
        return plan

    @staticmethod
    def _deny_plan(plugin_id: str, reason: str, rejection_reason: str) -> dict[str, Any]:
        return {
            "decision": "DENY",
            "reason": reason,
            "calls": [],
            "rejected_calls": [{"plugin_id": plugin_id, "reason": rejection_reason}],
            "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
        }

plugin_manager = PluginManager()
