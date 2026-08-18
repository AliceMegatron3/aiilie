<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api, unwrap } from '../api'

// ============================================================
// TaskStatusTracker — P4-4 前端全状态可视化
// 对接后端全状态机：排队→资源加载→分段执行→插件调度→模型推理→任务收尾
//                    →完成/异常终止/用户取消/失败
// 数据来源：任务详情 API（GET /api/v1/tasks/{id}）+ 系统资源/队列 API。
// 纯展示组件：props 驱动，向后端状态机完全对齐，无自有状态篡改。
// ============================================================

const props = defineProps({
  taskId: { type: String, default: '' },
  pollMs: { type: Number, default: 2000 },
})

// 全状态机定义（与后端 P2-3 细化状态机对齐）
const STAGES = [
  { key: 'PENDING', label: '排队等待', icon: '⏳' },
  { key: 'INITIALIZING', label: '资源加载', icon: '📦' },
  { key: 'SEGMENTING', label: '分段执行', icon: '✂️' },
  { key: 'PLUGIN_SCHEDULING', label: '插件调度', icon: '🧩' },
  { key: 'MODEL_INFERRING', label: '模型推理', icon: '🧠' },
  { key: 'FINALIZING', label: '任务收尾', icon: '🧹' },
]
const TERMINAL_OK = ['COMPLETED']
const TERMINAL_BAD = ['ABORTED', 'CANCELED', 'FAILED']

const task = ref(null)
const taskError = ref('')
const elapsed = ref(0)
const resourceStats = ref(null)
const queueInfo = ref(null)
let timer = null
let elapsedTimer = null

// 状态→阶段索引（未知状态按 0 处理，保证进度条可用）
const stageIndex = computed(() => {
  const s = task.value?.status || 'PENDING'
  const idx = STAGES.findIndex((x) => x.key === s)
  return idx >= 0 ? idx : 0
})
const progress = computed(() => {
  if (!task.value) return 0
  const total = STAGES.length + 1
  if (TERMINAL_OK.includes(task.value.status)) return 100
  if (TERMINAL_BAD.includes(task.value.status)) return Math.round(((stageIndex.value + 1) / total) * 100)
  return Math.round(((stageIndex.value + 0.5) / total) * 100)
})
const isTerminal = computed(() => {
  const s = task.value?.status || ''
  return TERMINAL_OK.includes(s) || TERMINAL_BAD.includes(s)
})
// 状态徽标配色（异常=红系，完成=绿系，进行中=青系）
const statusClass = computed(() => {
  const s = task.value?.status || 'PENDING'
  if (TERMINAL_OK.includes(s)) return 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10'
  if (TERMINAL_BAD.includes(s)) return 'text-red-400 border-red-500/40 bg-red-500/10'
  return 'text-cyan-400 border-cyan-500/40 bg-cyan-500/10'
})
const statusLabel = computed(() => {
  const s = task.value?.status || 'PENDING'
  const hit = STAGES.find((x) => x.key === s)
  if (hit) return hit.label
  if (s === 'COMPLETED') return '已完成'
  if (s === 'FAILED') return '执行失败'
  if (s === 'CANCELED') return '用户取消'
  if (s === 'ABORTED') return '异常终止'
  return s
})
const formattedElapsed = computed(() => {
  const m = Math.floor(elapsed.value / 60)
  const s = elapsed.value % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})
// 剩余预估（按平均阶段耗时估算，终态清零）
const estimateRemaining = computed(() => {
  if (!task.value || isTerminal.value) return '--'
  const done = stageIndex.value + 1
  const per = elapsed.value > 0 ? elapsed.value / Math.max(done, 1) : 0
  const remain = Math.max(0, (STAGES.length - done) * per)
  const m = Math.floor(remain / 60)
  const s = Math.round(remain % 60)
  return `约 ${m} 分 ${s} 秒`
})
// 异常状态可视化提示：取消/超时/熔断/报错 → 原因 + 建议
const issueHint = computed(() => {
  const t = task.value
  if (!t) return null
  const s = t.status || ''
  const err = t.error_message || t.error || ''
  if (s === 'CANCELED') {
    return { icon: '🛑', title: '任务已被取消', tip: '底层请求已同步终止，算力资源已释放，可重新发起任务。' }
  }
  if (s === 'ABORTED' && /超时|timeout/i.test(err)) {
    return { icon: '⏱️', title: '任务超时熔断', tip: '推理超时已触发熔断保护，请拆分任务或降低单次生成规模后重试。' }
  }
  if (s === 'ABORTED') {
    return { icon: '⚡', title: '任务异常终止', tip: '系统已自动终止并释放资源，建议查看后台日志溯源后重试。' }
  }
  if (s === 'FAILED') {
    return { icon: '❌', title: '任务执行失败', tip: `失败原因：${err || '未知错误'}。修正后重新提交即可。` }
  }
  return null
})

async function fetchTask() {
  if (!props.taskId) return
  try {
    const res = await api.tasks.detail(props.taskId)
    task.value = res?.data ?? res
    taskError.value = ''
  } catch (e) {
    taskError.value = '任务状态获取失败，请检查任务是否存在'
  }
}
async function fetchResources() {
  try {
    const [statsRes, queueRes] = await Promise.all([
      api.system.resourceStats(),
      api.system.queue(),
    ])
    resourceStats.value = unwrap<any>(statsRes)
    queueInfo.value = unwrap<any>(queueRes)
  } catch (e) {
    // 资源面板降级：获取失败仅隐藏面板，不影响任务主展示
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(async () => {
    await fetchTask()
    if (!isTerminal.value) await fetchResources()
  }, props.pollMs)
  elapsedTimer = setInterval(() => {
    if (!isTerminal.value) elapsed.value += 1
  }, 1000)
}
function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
}

watch(() => props.taskId, (id) => {
  elapsed.value = 0
  task.value = null
  taskError.value = ''
  if (id) {
    fetchTask()
    fetchResources()
    startPolling()
  } else {
    stopPolling()
  }
})
onMounted(() => { if (props.taskId) { fetchTask(); fetchResources(); startPolling() } })
onUnmounted(stopPolling)
</script>

<template>
  <div class="rounded-xl border border-slate-700/60 bg-slate-900/60 p-4 space-y-4">
    <!-- 头部：任务标识 + 状态徽标 -->
    <div class="flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-sm text-slate-400">任务</span>
        <span class="text-sm font-mono text-slate-200 truncate">{{ taskId || '未绑定' }}</span>
      </div>
      <span class="px-2.5 py-1 rounded-full border text-xs font-medium shrink-0" :class="statusClass">
        {{ statusLabel }}
      </span>
    </div>

    <!-- 全状态机进度条：排队→资源加载→分段执行→插件调度→模型推理→收尾 -->
    <div>
      <div class="flex items-center justify-between mb-1.5">
        <span class="text-xs text-slate-400">任务进度</span>
        <span class="text-xs font-mono text-cyan-400">{{ progress }}%</span>
      </div>
      <div class="w-full h-2 rounded-full bg-slate-800 overflow-hidden">
        <div
          class="h-full rounded-full transition-all duration-500"
          :class="TERMINAL_BAD.includes(task?.status) ? 'bg-red-500' : 'bg-gradient-to-r from-cyan-500 to-emerald-400'"
          :style="{ width: progress + '%' }"
        />
      </div>
      <div class="grid grid-cols-6 gap-1 mt-2">
        <div
          v-for="(st, i) in STAGES"
          :key="st.key"
          class="flex flex-col items-center gap-0.5"
        >
          <span class="text-xs" :class="i <= stageIndex && !isTerminal || (isTerminal && i < stageIndex) ? '' : 'opacity-40'">
            {{ st.icon }}
          </span>
          <span
            class="text-[10px] leading-tight text-center"
            :class="i === stageIndex && !isTerminal ? 'text-cyan-400 font-medium' : i < stageIndex ? 'text-emerald-400' : 'text-slate-500'"
          >{{ st.label }}</span>
        </div>
      </div>
    </div>

    <!-- 实时运行状态同步：耗时 / 当前阶段 / 剩余预估 -->
    <div class="grid grid-cols-3 gap-2 text-center">
      <div class="rounded-lg bg-slate-800/60 py-2">
        <div class="text-[10px] text-slate-500">已耗时</div>
        <div class="text-sm font-mono text-slate-200">{{ formattedElapsed }}</div>
      </div>
      <div class="rounded-lg bg-slate-800/60 py-2">
        <div class="text-[10px] text-slate-500">当前阶段</div>
        <div class="text-sm text-slate-200">{{ statusLabel }}</div>
      </div>
      <div class="rounded-lg bg-slate-800/60 py-2">
        <div class="text-[10px] text-slate-500">剩余预估</div>
        <div class="text-sm font-mono text-slate-200">{{ estimateRemaining }}</div>
      </div>
    </div>

    <!-- 异常状态可视化提示 -->
    <div v-if="issueHint" class="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 flex items-start gap-2">
      <span class="text-base leading-none mt-0.5">{{ issueHint.icon }}</span>
      <div class="min-w-0">
        <div class="text-sm text-red-300 font-medium">{{ issueHint.title }}</div>
        <div class="text-xs text-red-200/70 mt-0.5">{{ issueHint.tip }}</div>
      </div>
    </div>
    <div v-if="taskError" class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
      {{ taskError }}
    </div>

    <!-- 资源状态可视化：模型负载 / 算力占用 / 队列积压 -->
    <div v-if="resourceStats || queueInfo" class="grid grid-cols-3 gap-2 text-center">
      <div class="rounded-lg bg-slate-800/40 py-2">
        <div class="text-[10px] text-slate-500">CPU</div>
        <div class="text-sm font-mono text-slate-200">{{ resourceStats?.cpu_percent ?? '--' }}%</div>
      </div>
      <div class="rounded-lg bg-slate-800/40 py-2">
        <div class="text-[10px] text-slate-500">内存</div>
        <div class="text-sm font-mono text-slate-200">{{ resourceStats?.memory_percent ?? '--' }}%</div>
      </div>
      <div class="rounded-lg bg-slate-800/40 py-2">
        <div class="text-[10px] text-slate-500">队列积压</div>
        <div class="text-sm font-mono text-slate-200">{{ queueInfo?.pending_tasks_count ?? '--' }}</div>
      </div>
    </div>
  </div>
</template>
