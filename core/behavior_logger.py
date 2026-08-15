"""
core/behavior_logger.py — 批次7：用户行为日志采集模块
=======================================================
记录三类行为：
1. 操作路径（action_path）：用户点击/提交等操作链路
2. 页面停留（page_stay）：页面进入/离开与停留时长
3. 报错路径（error_path）：前端报错堆栈与路由上下文

存储策略：轮转存储（按大小轮转，保留 N 个备份），
落盘 %APPDATA%/No0_AI_V4/data/logs/behavior.log（JSONL）。

设计要点：直接文件写入（线程安全锁保护），不依赖 logging 框架，
避免全局日志级别配置导致行为数据静默丢失。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config_manager import config_manager
from core.path_resolver import get_log_dir

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BehaviorLogger:
    """线程安全的行为日志采集器（轮转存储，直接文件写入）。"""

    def __init__(self) -> None:
        self._log_path: Path | None = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with _lock:
            if self._initialized:
                return
            log_dir = get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / "behavior.log"
            self._max_bytes = config_manager.get_int(
                "deep_thinking.behavior_log_max_bytes", 512 * 1024
            )
            self._backup_count = config_manager.get_int(
                "deep_thinking.behavior_log_backup_count", 3
            )
            self._initialized = True

    def log_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """记录行为事件。event_type: action_path / page_stay / error_path / generic。"""
        self._ensure_initialized()
        record = {
            "ts": _now_iso(),
            "event_type": event_type,
            "payload": payload or {},
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _lock:
            try:
                self._rotate_if_needed()
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as exc:  # 日志失败不影响业务
                logger.warning("[BehaviorLogger] 行为日志写入失败: %s", exc)

    def _rotate_if_needed(self) -> None:
        """按大小轮转：主文件超过上限时重命名轮转，淘汰最旧备份。"""
        assert self._log_path is not None
        try:
            if not self._log_path.exists():
                return
            if self._log_path.stat().st_size < self._max_bytes:
                return
        except OSError:
            return
        # 轮转：behavior.log → behavior.log.1 → behavior.log.2 ...
        for i in range(self._backup_count - 1, 0, -1):
            src = self._log_path.with_name(f"behavior.log.{i}")
            dst = self._log_path.with_name(f"behavior.log.{i + 1}")
            if src.exists():
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        backup = self._log_path.with_name("behavior.log.1")
        try:
            os.replace(self._log_path, backup)
        except OSError:
            pass

    # ── 语义化快捷入口 ──────────────────────────────────────────

    def log_action_path(self, path: str, **extra: Any) -> None:
        self.log_event("action_path", {"path": path, **extra})

    def log_page_stay(self, page: str, route: str, duration_ms: int) -> None:
        self.log_event(
            "page_stay", {"page": page, "route": route, "duration_ms": duration_ms}
        )

    def log_error_path(self, route: str, error: str, stack: str = "") -> None:
        self.log_event("error_path", {"route": route, "error": error, "stack": stack[:2000]})

    # ── 读取（供 SoftwareArchitectAnalyzer 分析） ─────────────────

    def read_events(self, limit: int = 500) -> list[dict[str, Any]]:
        """读取最近 N 条行为事件（含轮转备份文件）。"""
        self._ensure_initialized()
        assert self._log_path is not None
        events: list[dict[str, Any]] = []
        files = sorted(self._log_path.parent.glob("behavior.log*"), key=lambda p: _file_mtime(p))
        for path in files[-6:]:  # 最多回读 6 个文件（主文件 + 备份）
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
        return events[-limit:]


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# 模块级单例
behavior_logger = BehaviorLogger()
