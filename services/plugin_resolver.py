"""插件执行前的确定性解析器。

只做 manifest 级别的依赖、冲突、能力与预算校验；不导入、不启动、不执行插件。
"""
from __future__ import annotations

from typing import Any


class PluginResolutionError(ValueError):
    pass


class PluginResolver:
    def __init__(self, manifests: list[dict[str, Any]]) -> None:
        self.manifests = {str(item.get("plugin_id") or item.get("id")): item for item in manifests}

    @staticmethod
    def _dependency_id(value: Any) -> str:
        if isinstance(value, str):
            return value.split("@", 1)[0]
        if isinstance(value, dict):
            return str(value.get("plugin_id") or value.get("id") or "")
        return ""

    def resolve(
        self,
        requested: list[str],
        required_capabilities: list[str] | None = None,
        caller_capabilities: list[str] | None = None,
        budget: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        required_capabilities = set(required_capabilities or [])
        caller_capabilities = set(caller_capabilities or [])
        budget = budget or {}
        errors: list[str] = []
        warnings: list[str] = []
        selected: set[str] = set()
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in visited:
                return
            if plugin_id in visiting:
                errors.append(f"检测到循环依赖: {plugin_id}")
                return
            manifest = self.manifests.get(plugin_id)
            if manifest is None:
                errors.append(f"依赖插件不存在: {plugin_id}")
                return
            visiting.add(plugin_id)
            for raw in manifest.get("requires", []) or []:
                dependency = self._dependency_id(raw)
                if dependency:
                    visit(dependency)
            visiting.remove(plugin_id)
            visited.add(plugin_id)
            selected.add(plugin_id)

        for plugin_id in requested:
            visit(plugin_id)

        for plugin_id in sorted(selected):
            manifest = self.manifests[plugin_id]
            if manifest.get("trust_state") != "TRUSTED":
                errors.append(f"插件未信任，禁止执行: {plugin_id}")
            declared = set(manifest.get("capabilities", []) or [])
            missing = required_capabilities - declared
            if missing:
                errors.append(f"插件 {plugin_id} 缺少能力: {sorted(missing)}")
            denied = declared - caller_capabilities if caller_capabilities else set()
            if denied:
                errors.append(f"调用者未授权插件 {plugin_id} 的能力: {sorted(denied)}")
            budget_spec = manifest.get("resource_budget", {}) or {}
            for field in ("timeout_seconds", "max_memory_mb", "max_output_bytes", "max_tokens"):
                requested_limit = budget.get(field)
                declared_limit = budget_spec.get(field)
                if requested_limit is not None and declared_limit is not None and requested_limit > declared_limit:
                    errors.append(f"插件 {plugin_id} 的 {field} 超出 manifest 预算")

            conflicts = {self._dependency_id(item) for item in manifest.get("conflicts", []) or []}
            overlap = conflicts.intersection(selected)
            if overlap:
                errors.append(f"插件 {plugin_id} 与已选插件冲突: {sorted(overlap)}")

        return {
            "decision": "DENY" if errors else "ALLOW",
            "selected": sorted(selected),
            "errors": errors,
            "warnings": warnings,
            "execution": False,
            "reason": "仅完成执行前解析，未启动插件",
        }
