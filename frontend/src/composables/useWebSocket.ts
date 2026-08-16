/**
 * composables/useWebSocket.js — WebSocket 连接管理（增强版）
 * ============================================================
 * 优化点：
 * 1. 断线重连改指数退避（1s→2s→4s→…→上限30s），成功后重置；
 *    替代原 RightPanel 固定 3 秒重连的粗暴策略。
 * 2. 心跳：自动应答服务端 ping（{"action":"ping"}），保活连接。
 * 3. 长任务流式订阅：subscribeTask(taskId, handler) 订阅后端
 *    task_progress 事件（批次1引擎消费循环推送）。
 * 4. 组件卸载时彻底关闭（不再泄漏定时器与连接）。
 */
import { ref, onUnmounted } from 'vue'
import { getWsBaseURL } from '../utils/electron'

type WsData = Record<string, any>
type TaskHandler = (event: WsData) => void
interface WebSocketOptions {
  onMessage?: (data: WsData) => void
  onReconnect?: () => void | Promise<void>
  sessionId?: string
}

export function useWebSocket({ onMessage, onReconnect, sessionId = 'default_global' }: WebSocketOptions = {}) {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let retryDelay = 1000 // 指数退避起始 1s
  const MAX_RETRY_DELAY = 30000
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let manuallyClosed = false
  const taskHandlers = new Map<string, TaskHandler>()
  let hasReconnected = false

  const sendTaskSubscription = (socket: WebSocket, taskId: string) => {
    if (socket.readyState !== WebSocket.OPEN) return
    try {
      socket.send(JSON.stringify({ action: 'subscribe_task', task_id: taskId }))
    } catch (_e) {
      // 连接在检查 readyState 后关闭时忽略发送失败，onclose 会负责重连。
    }
  }

  const restoreTaskSubscriptions = (socket: WebSocket) => {
    for (const taskId of taskHandlers.keys()) {
      sendTaskSubscription(socket, taskId)
    }
  }

  const url = `${getWsBaseURL()}/api/v1/ws?session_id=${encodeURIComponent(sessionId)}`

  const connect = () => {
    if (manuallyClosed) return
    try {
      const socket = new WebSocket(url)
      ws = socket
    } catch (_e) {
      scheduleReconnect()
      return
    }

    const socket = ws
    if (!socket) return

    socket.onopen = () => {
      connected.value = true
      retryDelay = 1000 // 连接成功，退避重置
      // 服务端会在断开时清理旧连接的订阅；新连接建立后必须恢复本地订阅集合。
      restoreTaskSubscriptions(socket)
      if (hasReconnected && onReconnect) {
          onReconnect()
      }
      hasReconnected = true
    }

    socket.onmessage = (event) => {
      let data: WsData
      try {
        data = JSON.parse(event.data) as WsData
      } catch (_e) {
        return
      }

      // 服务端心跳 ping → 应答（任何消息都算活动信号）
      if (data.type === 'ping') {
        try {
          socket.send(JSON.stringify({ action: 'ping' }))
        } catch (e) { /* 连接已关闭时忽略 */ }
        return
      }

      // 长任务流式进度 → 分发给已订阅 handler
      if (data.type === 'task_progress' && data.task_id && taskHandlers.has(data.task_id)) {
        taskHandlers.get(data.task_id)?.(data)
        return
      }

      if (onMessage) onMessage(data)
    }

    socket.onclose = () => {
      if (ws !== socket) return
      connected.value = false
      scheduleReconnect()
    }

    socket.onerror = () => {
      // onclose 会紧随其后触发，无需重复处理
    }
  }

  const scheduleReconnect = () => {
    if (manuallyClosed || retryTimer) return
    retryTimer = setTimeout(() => {
      retryTimer = null
      // 指数退避：1s → 2s → 4s → 8s → ... → 30s 封顶
      retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY)
      connect()
    }, retryDelay)
  }

  /**
   * 订阅长任务进度流。
   * @param {string} taskId 批次1任务ID
   * @param {(evt: {status: string, segments: Array}) => void} handler
   */
  const subscribeTask = (taskId: string, handler: TaskHandler) => {
    taskHandlers.set(taskId, handler)
    if (ws && ws.readyState === WebSocket.OPEN) {
      sendTaskSubscription(ws, taskId)
    }
  }

  const unsubscribeTask = (taskId: string) => {
    taskHandlers.delete(taskId)
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'unsubscribe_task', task_id: taskId }))
    }
  }

  const close = () => {
    manuallyClosed = true
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    connected.value = false
  }

  connect()

  onUnmounted(close)

  return { connected, subscribeTask, unsubscribeTask, close }
}
