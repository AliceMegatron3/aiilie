<template>
  <div class="h-full flex flex-col w-full">
    <!-- 主视图: AI 对话框 -->
    <div class="flex-1 flex flex-col min-h-0 bg-[#18181b]">
      <!-- 对话折叠控制头 -->
      <div
        @click="isChatExpanded = !isChatExpanded"
        class="flex items-center justify-between p-2 cursor-pointer bg-[#222225] hover:bg-[#27272a] transition-colors border-b border-[#2a2a30] select-none text-xs font-semibold text-gray-300 shrink-0"
      >
        <span class="flex items-center space-x-2">
          <span>AI 对话会话区</span>
          <span v-show="!isChatExpanded" class="text-[10px] text-gray-500 font-normal">(已折叠)</span>
        </span>
        <!-- 连接状态指示（WebSocket） -->
        <span class="flex items-center space-x-1.5">
          <span class="w-1.5 h-1.5 rounded-full" :class="wsConnected ? 'bg-emerald-500' : 'bg-red-500 animate-pulse'"></span>
          <span class="text-[9px] text-gray-600 font-normal font-mono">{{ wsConnected ? '实时' : '重连中' }}</span>
          <span class="transform transition-transform text-gray-500" :class="isChatExpanded ? 'rotate-180' : ''">▼</span>
        </span>
      </div>

      <!-- 消息列表（拆分组件） -->
      <ChatMessageList
        ref="chatListRef"
        :messages="messages"
        :is-processing="isProcessing"
        :expanded="isChatExpanded"
      />
    </div>

    <!-- 指令输入（拆分组件） -->
    <CommandInputBox
      :is-processing="isProcessing"
      :mode="currentMode"
      @send="sendCommand"
      @mode-change="updateProjectMode"
    />

    <!-- 监控面板（拆分组件） -->
    <MonitorDrawer :queue-info="queueInfo" @interrupt="triggerInterrupt" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, unwrap } from '../api'
import { toast } from '../utils/toast'
import { useWebSocket } from '../composables/useWebSocket'
import { useVisibilityPolling } from '../composables/useVisibilityPolling'
import { useProjectStore } from '../stores/useProjectStore'
import ChatMessageList from './panels/ChatMessageList.vue'
import CommandInputBox from './panels/CommandInputBox.vue'
import MonitorDrawer from './panels/MonitorDrawer.vue'

const projectStore = useProjectStore()

// 展开/折叠状态
const isChatExpanded = ref(true)

// 模式: rapid | think | complex
type Mode = 'rapid' | 'think' | 'complex'
interface ChatMessage {
  role: 'ai' | 'user'
  content: string
  thinking_progress: string
  quantize_progress?: {
    book_id: string
    completed: number
    total: number
    cards_in_seg: number
    percent: number
    round_label: string
  }
}
const currentMode = ref<Mode>('rapid')

const messages = ref<ChatMessage[]>([{ role: 'ai', content: '系统已就绪。支持快速(Rapid)与深思(Think)双模式切换。', thinking_progress: '' }])
const isProcessing = ref(false)
const chatListRef = ref<InstanceType<typeof ChatMessageList> | null>(null)
const queueInfo = ref(null)
// 长任务流式进度：当前激活任务ID（修复：声明须在 loadLocalState/useWebSocket 之前，
// 否则触发 TDZ 暂时性死区，导致页面加载即抛 ReferenceError）
const activeTaskId = ref(null)

const scrollToBottom = () => chatListRef.value?.scrollToBottom()

// ── WebSocket：指数退避重连 + 心跳应答 + 长任务订阅 ──
const { connected: wsConnected, subscribeTask, unsubscribeTask } = useWebSocket({
  onReconnect: async () => {
    if (activeTaskId.value) {
      try {
        const res = await api.tasks.detail(activeTaskId.value)
        const target = messages.value[messages.value.length - 1]
        if (target && target.role === 'ai') {
          target.content = `[任务恢复] ${res.data.status} · 已恢复进度`
        }
      } catch (e) {
        // failed to fetch task detail
      }
    }
  },
  onMessage: (data: any) => {
    if (data.type === 'thinking_progress' && messages.value.length > 0) {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg.role === 'ai') {
        lastMsg.thinking_progress = (lastMsg.thinking_progress || '') + (data.content || data.message || '') + '\n'
        scrollToBottom()
      }
    } else if (data.type === 'chat_chunk' && messages.value.length > 0) {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg.role === 'ai') {
        lastMsg.content += data.content
        scrollToBottom()
      }
    } else if (data.type === 'quantize_start') {
      const roundMap: Record<number, string> = { 1: '粗扫', 2: '深挖', 3: '精炼' }
      const roundText = roundMap[data.round as number] || `R${data.round}`
      messages.value.push({
        role: 'ai',
        content: `[量化启动] 正在量化《${data.book_id}》，轮次：${roundText}，模式：${data.mode || 'both'}，模型：${data.model || '默认'}`,
        thinking_progress: '',
        quantize_progress: {
          book_id: data.book_id,
          completed: 0,
          total: 0,
          cards_in_seg: 0,
          percent: 0,
          round_label: roundText,
        }
      })
      scrollToBottom()
    } else if (data.type === 'quantize_progress') {
      // 在已有量化启动消息上更新进度条，而非新增消息
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.role === 'ai' && lastMsg.quantize_progress) {
        lastMsg.quantize_progress.completed = data.completed
        lastMsg.quantize_progress.total = data.total
        lastMsg.quantize_progress.cards_in_seg = data.cards_in_seg
        lastMsg.quantize_progress.percent = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0
      } else {
        messages.value.push({
          role: 'ai',
          content: '',
          thinking_progress: '',
          quantize_progress: {
            book_id: data.book_id,
            completed: data.completed,
            total: data.total,
            cards_in_seg: data.cards_in_seg,
            percent: data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0,
            round_label: '',
          }
        })
      }
      scrollToBottom()
    } else if (data.type === 'quantize_done') {
      // 完成时将进度固定到 100%
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.role === 'ai' && lastMsg.quantize_progress) {
        lastMsg.quantize_progress.percent = 100
        lastMsg.quantize_progress.completed = lastMsg.quantize_progress.total
        lastMsg.quantize_progress.round_label = '完成'
      }
      messages.value.push({
        role: 'ai',
        content: `[量化完成] 书籍《${data.book_id}》量化已完成，已触发反思学习`,
        thinking_progress: ''
      })
      scrollToBottom()
    }
  },
})

// ── 缓存本地状态 ──
const loadLocalState = () => {
  const cached = localStorage.getItem('aiilie_chat_state')
  if (cached) {
    try {
      const parsed = JSON.parse(cached)
      if (parsed.messages && parsed.messages.length > 0) messages.value = parsed.messages
      if (parsed.activeTaskId) activeTaskId.value = parsed.activeTaskId
      
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg && lastMsg.role === 'ai' && parsed.activeTaskId) {
        if (!wsConnected.value) {
          lastMsg.content = `[等待恢复] 离线中，重新连接后将续接...`
        }
      }
    } catch(e) {}
  }
}
loadLocalState()

const saveLocalState = () => {
  localStorage.setItem('aiilie_chat_state', JSON.stringify({
    messages: messages.value,
    activeTaskId: activeTaskId.value
  }))
}
watch(messages, saveLocalState, { deep: true })
watch(activeTaskId, saveLocalState)


// 长任务流式进度：订阅任务频道并在对话中追加进度回执
watch(activeTaskId, (taskId, oldTaskId) => {
  if (oldTaskId) {
    unsubscribeTask(oldTaskId)
  }
  if (taskId) {
    subscribeTask(taskId, (evt: any) => {
      const target = messages.value[messages.value.length - 1]
      if (target && target.role === 'ai') {
        target.content = `[任务进度] ${evt.status} · 分段 ${evt.segments?.length || 0} 个`
      }
    })
  }
})

// 监听项目变化，读取 default_compute_mode
watch(() => projectStore.currentProject, (newProj) => {
  if (newProj && newProj.default_compute_mode) {
    currentMode.value = newProj.default_compute_mode
  }
}, { immediate: true })

const updateProjectMode = async (newMode?: string) => {
  // 接收 CommandInputBox 发出的新模式并同步本地状态（修复：原先忽略参数导致切换失效）
  if (newMode) {
    if (newMode === 'rapid' || newMode === 'think' || newMode === 'complex') {
      currentMode.value = newMode
    }
  }

  if (currentMode.value === 'complex') {
    // 统一 Toast 确认框替代 window.confirm
    const confirmed = await toast.confirm('⚠️ 警告：繁杂模式 (Complex Mode) 将消耗数倍甚至十倍以上的算力与时间进行深度的发散思考与自我论证。您的项目不会因为超时崩溃，但产生结果的时间会很漫长。您确定要开启此模式吗？')
    if (!confirmed) {
      currentMode.value = 'think' // 回退到深度思考模式
      return
    }
    // complex 是"本次指令"级模式，不持久化到项目（后端 default_compute_mode 仅 rapid/think）
    return
  }

  if (projectStore.currentProjectId) {
    try {
      if (currentMode.value === 'rapid' || currentMode.value === 'think') {
        await projectStore.switchMode(projectStore.currentProjectId, currentMode.value)
      }
    } catch (e) {
      console.warn('更新模式到项目失败', e)
    }
  }
}

// ── 队列状态轮询（页面可见时触发） ──
const pollQueueStatus = async () => {
  try {
    const res = await api.system.queue()
    queueInfo.value = unwrap<any>(res)
  } catch (e) {
    // 静默失败
  }
}

useVisibilityPolling(pollQueueStatus, 10000)

const loadLatestChatHistory = async () => {
  try {
    const listRes = await api.sessions.list()
    if (listRes.data.status === 'success' && listRes.data.data && listRes.data.data.length > 0) {
      // 取最新的活动会话加载历史
      const latestSession = listRes.data.data[0]
      const histRes = await api.system.chatHistory(latestSession.session_id)
      const history = unwrap<any>(histRes)
      if (Array.isArray(history) && history.length > 0) {
        messages.value = history.map(m => ({
          role: m.role,
          content: m.content,
          thinking_progress: m.thinking_progress || ''
        }))
        scrollToBottom()
      }
    }
  } catch (e) {
    console.error('加载聊天历史失败', e)
  }
}

const sendCommand = async (text: string) => {
  if (!text) return

  messages.value.push({ role: 'user', content: text, thinking_progress: '' })
  isProcessing.value = true
  scrollToBottom()

  try {
    const res = await api.system.command(text, { mode: currentMode.value }, projectStore.currentProjectId)

    // 【Phase 9】处理算力霸权锁拦截（统一 Toast 替代 alert）
    if (res.data.status === 'blocked' && res.data.reason === 'QUANTIZATION_LOCK_ACTIVE') {
      toast.warning(res.data.message || '系统正在全力进行书库量化，创作通道暂时挂起。')
      messages.value.pop()
      isProcessing.value = false
      return
    }

    // B 类收口：统一指令网关对项目管理/文档学习返回 bypassed——不伪装"任务已分发/ID: N/A"，
    // 由前端项目/文档面板直接调用真实 API（项目管理 → 项目面板；文档学习 → 文档编辑器内 /docs/{id}/learn）。
    if (res.data.status === 'bypassed') {
      messages.value.push({
        role: 'ai',
        content: `[系统回执] ${res.data.message || '该指令已绕过统一指令网关，请使用对应前端面板直接操作（bypassed）。'}`,
        thinking_progress: ''
      })
      return
    }

    // 如果后端直接返回内容，则添加到消息
    if (res.data.status === 'success' && res.data.content) {
      messages.value.push({ role: 'ai', content: res.data.content, thinking_progress: res.data.thinking_progress || '' })
    } else {
      // 后端采用任务派发：创建占位回复，并订阅任务进度流
      const taskId = res.data.task_id || res.data.session_id
      if (taskId) activeTaskId.value = taskId
      messages.value.push({
        role: 'ai',
        content: `[系统回执] 任务已${res.data.status === 'queued' ? '排队' : '分发'}\n任务ID: ${taskId || 'N/A'}`,
        thinking_progress: ''
      })
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    messages.value.push({
      role: 'ai',
      content: `[网络异常] 无法连接到中枢系统。详细信息: ${message}`,
      thinking_progress: ''
    })
    toast.error('指令发送失败，请检查后端服务状态。')
  } finally {
    isProcessing.value = false
    scrollToBottom()
  }
}

const triggerInterrupt = async () => {
  try {
    await api.system.interrupt()
    messages.value.push({ role: 'ai', content: '[熔断] 用户主动下达全域熔断指令，后台调度已强行回归 IDLE 态。', thinking_progress: '' })
    toast.info('全局中断信号已广播')
    scrollToBottom()
  } catch (e) {
    console.error(e)
  }
}

loadLatestChatHistory()
</script>
