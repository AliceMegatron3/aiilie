"""
api/websocket.py — 实时双向通信通道（增强版）
================================================
1. 会话房间单播隔离（chat_chunk / thinking_progress）
2. 长任务流式输出：task 频道订阅/发布（task_progress 事件），
   批次1引擎消费循环在任务生命周期节点自动推送进度
3. 心跳保活：30s ping/pong 检测死连接并回收
4. 日志统一惰性格式化（% 风格，避免未触发级别时的字符串拼接开销）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# 心跳间隔（秒）
_HEARTBEAT_INTERVAL = 30
# 等待客户端 pong 的超时（秒）
_HEARTBEAT_TIMEOUT = 10


class ConnectionManager:
    """会话房间 + 任务频道双维度的连接管理器。"""

    def __init__(self) -> None:
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.task_subscribers: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(session_id, set()).add(websocket)
        logger.info(
            "WebSocket 客户端已连接 (Session: %s). 当前房间连接数: %d",
            session_id, len(self.active_connections[session_id]),
        )

    def disconnect(self, websocket: WebSocket, session_id: str) -> None:
        if session_id in self.active_connections:
            self.active_connections[session_id].discard(websocket)
            logger.info(
                "WebSocket 客户端已断开 (Session: %s). 当前房间连接数: %d",
                session_id, len(self.active_connections[session_id]),
            )
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        # 同步清理任务频道订阅
        for subs in self.task_subscribers.values():
            subs.discard(websocket)
        self.task_subscribers = {
            k: v for k, v in self.task_subscribers.items() if v
        }

    # ── 任务频道（长任务流式输出） ─────────────────────────────
    def subscribe_task(self, task_id: str, websocket: WebSocket) -> None:
        self.task_subscribers.setdefault(task_id, set()).add(websocket)

    def unsubscribe_task(self, task_id: str, websocket: WebSocket) -> None:
        if task_id in self.task_subscribers:
            self.task_subscribers[task_id].discard(websocket)
            if not self.task_subscribers[task_id]:
                del self.task_subscribers[task_id]

    async def publish_task_event(
        self, task_id: str, event: dict[str, Any]
    ) -> None:
        """向订阅该任务的所有连接推送流式进度事件。"""
        subscribers = list(self.task_subscribers.get(task_id, set()))
        if not subscribers:
            return
        payload = json.dumps(
            {"type": "task_progress", "task_id": task_id, **event},
            ensure_ascii=False,
        )
        for connection in subscribers:
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.error("WebSocket 推送任务进度失败: %s", e)

    # ── 会话房间 ───────────────────────────────────────────────
    async def send_progress_to_session(
        self, session_id: str, task_id: str, stage: str, message: str, progress: str
    ) -> None:
        payload = json.dumps(
            {
                "type": "thinking_progress",
                "task_id": task_id,
                "stage": stage,
                "message": message,
                "progress": progress,
            },
            ensure_ascii=False,
        )
        await self._send_raw(session_id, payload)

    async def send_to_session(self, session_id: str, payload: dict | str) -> None:
        if isinstance(payload, str):
            payload = {"type": "chat_chunk", "content": payload}
        await self._send_raw(session_id, json.dumps(payload, ensure_ascii=False))

    async def broadcast(self, payload: dict) -> None:
        """用于系统全局告警广播。"""
        raw = json.dumps(payload, ensure_ascii=False)
        for session_id in list(self.active_connections.keys()):
            await self._send_raw(session_id, raw)

    async def _send_raw(self, session_id: str, raw: str) -> None:
        if session_id not in self.active_connections:
            logger.warning(
                "Session %s 不存在活动 WebSocket 连接, 丢弃消息.", session_id
            )
            return
        for connection in list(self.active_connections[session_id]):
            try:
                await connection.send_text(raw)
            except Exception as e:
                logger.error("WebSocket 发送聊天数据失败: %s", e)


manager = ConnectionManager()


async def _heartbeat(websocket: WebSocket) -> None:
    """周期性发送 ping；若客户端超过 2 个心跳周期无任何活动（含 pong），判定离线断开。

    实现说明：主循环每次收到消息会刷新 websocket.state.last_activity，
    心跳协程仅做"超时检测 + 发送 ping"，不与主循环竞争接收消息。
    """
    stale_limit = _HEARTBEAT_INTERVAL * 2 + _HEARTBEAT_TIMEOUT
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            return
        last = getattr(websocket.state, "last_activity", None)
        if last is None:
            websocket.state.last_activity = time.monotonic()
            continue
        if time.monotonic() - last > stale_limit:
            logger.warning("WebSocket 心跳超时，判定客户端离线，主动断开。")
            try:
                await websocket.close(code=4002)
            except Exception:
                pass
            return


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str = "default_global",
    token: str | None = None,
):
    """
    提供前后端实时双向通信：DEEP_THINK 进度推送、对话流转与长任务流式输出。
    客户端消息协议：
        {"action": "subscribe_task", "task_id": "..."}  订阅长任务进度
        {"action": "unsubscribe_task", "task_id": "..."} 退订
        {"action": "ping"}                               心跳应答

    鉴权：require_auth=true 时校验 token（query `?token=` 或 Authorization header），
    失败关闭连接（code=4001）；开发模式（require_auth=false）匿名放行。
    """
    from core.config_manager import config_manager
    from core.security import verify_api_token

    if bool(config_manager.get("security.require_auth", False)):
        auth_token = token or websocket.headers.get("Authorization", "").replace("Bearer ", "").strip()
        if not auth_token or not verify_api_token(auth_token):
            logger.warning("WebSocket 鉴权失败，拒绝连接 (Session: %s)", session_id)
            await websocket.close(code=4001)
            return

    await manager.connect(websocket, session_id)
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))
    try:
        while True:
            data = await websocket.receive_text()
            # 刷新活动时间戳（任何客户端消息都视为存活信号，包含 pong 应答）
            websocket.state.last_activity = time.monotonic()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                logger.info("收到 WS 非 JSON 消息 (Session: %s): %s", session_id, data)
                continue

            action = message.get("action")
            task_id = message.get("task_id")
            if action == "subscribe_task" and task_id:
                manager.subscribe_task(task_id, websocket)
            elif action == "unsubscribe_task" and task_id:
                manager.unsubscribe_task(task_id, websocket)
            elif action == "ping":
                continue
            else:
                logger.info("收到 WS 消息 (Session: %s): %s", session_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        manager.disconnect(websocket, session_id)
