"""
core/plugin_lifecycle.py — 插件生命周期状态机
=============================================
七态：INSTALLED → LOADING → ENABLED → RUNNING → ERROR → DISABLED → UNINSTALLED
"""
from __future__ import annotations

import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class PluginState(str, Enum):
    INSTALLED = "INSTALLED"
    LOADING = "LOADING"
    ENABLED = "ENABLED"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    DISABLED = "DISABLED"
    UNINSTALLED = "UNINSTALLED"


# 合法的状态转移表
_TRANSITIONS: dict[PluginState, set[PluginState]] = {
    PluginState.INSTALLED: {PluginState.LOADING},
    PluginState.LOADING: {PluginState.ENABLED, PluginState.ERROR},
    PluginState.ENABLED: {PluginState.RUNNING, PluginState.DISABLED, PluginState.UNINSTALLED},
    PluginState.RUNNING: {PluginState.ENABLED, PluginState.ERROR, PluginState.DISABLED, PluginState.UNINSTALLED},
    PluginState.ERROR: {PluginState.ENABLED, PluginState.DISABLED, PluginState.UNINSTALLED},
    PluginState.DISABLED: {PluginState.ENABLED, PluginState.UNINSTALLED},
    PluginState.UNINSTALLED: set(),
}


class PluginRecord:
    """单个插件的运行时状态记录。"""

    def __init__(self, plugin_id: str, manifest: dict) -> None:
        self.plugin_id = plugin_id
        self.manifest = manifest
        self.state: PluginState = PluginState.INSTALLED
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.last_transition: str = self.created_at
        self.error_message: Optional[str] = None

    def can_transition(self, target: PluginState) -> bool:
        return target in _TRANSITIONS.get(self.state, set())

    def transition(self, target: PluginState, error_message: str | None = None) -> None:
        if not self.can_transition(target):
            raise ValueError(f"非法状态转移: {self.state.value} → {target.value}")
        old = self.state
        self.state = target
        self.last_transition = datetime.now(timezone.utc).isoformat()
        self.error_message = error_message
        logger.info("[PluginLifecycle] %s: %s → %s", self.plugin_id, old.value, target.value)

    def to_dict(self) -> dict:
        return {
            "plugin_id": self.plugin_id,
            "state": self.state.value,
            "manifest": self.manifest,
            "created_at": self.created_at,
            "last_transition": self.last_transition,
            "error_message": self.error_message,
        }


class PluginLifecycleManager:
    """管理所有已安装插件的生命周期。"""

    def __init__(self) -> None:
        self._records: dict[str, PluginRecord] = {}

    def register(self, plugin_id: str, manifest: dict, replace: bool = False) -> PluginRecord:
        if plugin_id in self._records and not replace:
            return self._records[plugin_id]
        record = PluginRecord(plugin_id, manifest)
        self._records[plugin_id] = record
        return record

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._records.get(plugin_id)

    def list_all(self) -> list[dict]:
        return [r.to_dict() for r in self._records.values()]

    def set_loading(self, plugin_id: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.LOADING)

    def set_enabled(self, plugin_id: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.ENABLED)

    def set_error(self, plugin_id: str, msg: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.ERROR, error_message=msg)

    def set_disabled(self, plugin_id: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.DISABLED)

    def set_running(self, plugin_id: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.RUNNING)

    def uninstall(self, plugin_id: str) -> None:
        r = self._records.get(plugin_id)
        if r:
            r.transition(PluginState.UNINSTALLED)


plugin_lifecycle = PluginLifecycleManager()
