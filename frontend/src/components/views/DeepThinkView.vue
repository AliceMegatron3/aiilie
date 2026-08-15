<template>
  <div class="h-full w-full flex bg-[#1e1e24] text-gray-200 overflow-hidden">
    <!-- 左侧：任务提交 + 报告 -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
        <h2 class="text-lg font-bold text-gray-100 mb-3">🧠 深度思考与元认知</h2>
        <div class="flex gap-2">
          <input
            v-model="prompt"
            type="text"
            placeholder="输入创作指令/剧情问题，启动五阶段长思考流水线..."
            class="flex-1 bg-[#2a2a30] border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500"
            @keydown.enter="submitTask"
          />
          <select v-model="bindProjectId" class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none">
            <option value="">不绑定项目</option>
            <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
          </select>
          <button
            class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded text-sm transition-colors disabled:opacity-40"
            :disabled="running"
            @click="submitTask"
          >{{ running ? '思考中...' : '启动长思考' }}</button>
        </div>
      </div>

      <!-- 五阶段进度 -->
      <div v-if="running" class="px-6 py-3 border-b border-[#2a2a30] shrink-0 bg-[#1a1a1e]">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs text-gray-400">五阶段流水线：全景构建 → 冲突扫描 → 深度分析 → 交叉验证 → 生成报告</span>
          <span class="text-xs font-mono text-indigo-400">{{ progressPct.toFixed(0) }}%</span>
        </div>
        <div class="h-1.5 bg-[#2a2a30] rounded-full overflow-hidden">
          <div class="h-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-500" :style="{ width: progressPct + '%' }"></div>
        </div>
        <div class="flex gap-1.5 mt-2.5">
          <div
            v-for="(s, i) in stages"
            :key="s.key"
            class="flex-1 text-center px-1 py-1.5 rounded text-[10px] transition-colors"
            :class="stageClass(i)"
          >{{ s.label }}</div>
        </div>
        <p class="text-[11px] text-gray-400 mt-2">{{ progressMessage }}</p>
      </div>

      <!-- 报告区 -->
      <div class="flex-1 overflow-y-auto p-6">
        <div v-if="!report && !running" class="h-full flex flex-col items-center justify-center text-gray-600 gap-3">
          <span class="text-4xl">🧠</span>
          <span class="text-sm">提交创作指令后，系统将执行五阶段深度思考并产出结构化分析报告</span>
        </div>
        <div v-else-if="report" class="max-w-3xl mx-auto">
          <h3 class="text-base font-bold text-gray-100 mb-4">{{ report.title }}</h3>
          <div class="grid grid-cols-5 gap-2 mb-5">
            <div v-for="s in report.stages" :key="s.stage" class="bg-[#1f1f24] border border-gray-700 rounded p-2.5">
              <div class="text-[9px] text-gray-500 font-mono mb-1">{{ s.stage }}</div>
              <div class="text-[10px] text-indigo-400 mb-1">{{ s.label }}</div>
              <p class="text-[10px] text-gray-400 leading-snug">{{ s.summary }}</p>
            </div>
          </div>
          <div class="bg-[#1f1f24] border border-gray-700 rounded p-4 mb-4">
            <h4 class="text-xs font-bold text-amber-400 mb-2">发现的潜在问题（{{ report.found_issues.length }}）</h4>
            <ul v-if="report.found_issues.length" class="space-y-1.5">
              <li v-for="(issue, i) in report.found_issues" :key="i" class="text-xs text-gray-300 flex gap-2">
                <span class="text-amber-500 shrink-0">{{ i + 1 }}.</span>{{ issue }}
              </li>
            </ul>
            <p v-else class="text-xs text-gray-500">未发现显著问题</p>
          </div>
          <div class="bg-[#1f1f24] border border-gray-700 rounded p-4 mb-4">
            <h4 class="text-xs font-bold text-emerald-400 mb-2">深度分析与建议</h4>
            <ul class="space-y-1.5">
              <li v-for="(s, i) in report.suggestions" :key="i" class="text-xs text-gray-300 flex gap-2">
                <span class="text-emerald-500 shrink-0">›</span>{{ s }}
              </li>
            </ul>
          </div>
          <div class="bg-[#1f1f24] border border-gray-700 rounded p-4">
            <h4 class="text-xs font-bold text-sky-400 mb-2">交叉验证结论</h4>
            <p class="text-xs text-gray-300 whitespace-pre-line leading-relaxed">{{ report.conclusion }}</p>
          </div>
          <details class="mt-4">
            <summary class="text-[10px] text-gray-500 cursor-pointer hover:text-gray-400">思考轨迹还原（{{ report.thought_trace.length }} 条）</summary>
            <div class="mt-2 space-y-1.5 bg-[#1a1a1e] border border-[#2a2a30] rounded p-3">
              <p v-for="(t, i) in report.thought_trace" :key="i" class="text-[10px] text-gray-500 leading-relaxed">› {{ t }}</p>
            </div>
          </details>
        </div>
      </div>
    </div>

    <!-- 右侧：架构镜像 + 进化建议书 -->
    <div class="w-80 shrink-0 border-l border-[#2a2a30] overflow-y-auto p-4 space-y-4">
      <div>
        <h4 class="text-xs font-bold text-gray-300 mb-2">系统架构镜像</h4>
        <div class="grid grid-cols-3 gap-2">
          <div class="bg-[#1f1f24] border border-gray-700 rounded p-2 text-center" v-for="m in blueprintMetrics" :key="m.key">
            <div class="text-sm font-bold text-indigo-400">{{ m.value }}</div>
            <div class="text-[9px] text-gray-500">{{ m.label }}</div>
          </div>
        </div>
        <button class="mt-2 text-[10px] text-gray-500 hover:text-gray-300 underline" @click="loadBlueprint">刷新镜像</button>
      </div>
      <div>
        <h4 class="text-xs font-bold text-gray-300 mb-2 flex items-center justify-between">
          软件进化建议书
          <button class="text-[10px] text-indigo-400 hover:text-indigo-300" @click="loadTickets">分析</button>
        </h4>
        <div v-if="tickets.length === 0" class="text-[10px] text-gray-600">点击「分析」由元开发者分析器生成</div>
        <div v-for="t in tickets" :key="t.ticket_id" class="bg-[#1f1f24] border border-gray-700 rounded p-2.5 mb-2">
          <div class="flex items-center justify-between mb-1">
            <span class="px-1.5 py-0.5 rounded text-[9px]" :class="severityClass(t.severity)">{{ t.severity }}</span>
            <span class="text-[9px] text-gray-600 font-mono">{{ t.affected_module }}</span>
          </div>
          <p class="text-[10px] text-gray-300 leading-snug mb-1">{{ t.issue_description }}</p>
          <p class="text-[9px] text-emerald-400/80 leading-snug">→ {{ t.suggested_action }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { toast } from '../../utils/toast'
import { api } from '../../api'

const stages = [
  { key: 'MAPPING', label: '全景构建' },
  { key: 'SCANNING', label: '冲突扫描' },
  { key: 'ANALYZING', label: '深度分析' },
  { key: 'VALIDATING', label: '交叉验证' },
  { key: 'CONCLUDING', label: '生成报告' },
]

const prompt = ref('')
const bindProjectId = ref('')
const projects = ref([])
const running = ref(false)
const currentTaskId = ref('')
const progressPct = ref(0)
const progressMessage = ref('')
const stageIndex = ref(-1)
const report = ref(null)
const tickets = ref([])
const blueprint = ref(null)

const blueprintMetrics = computed(() => {
  const b = blueprint.value
  if (!b) return [{ key: 'arch', label: '模块', value: '-' }, { key: 'api', label: '路由', value: '-' }, { key: 'ui', label: '视图', value: '-' }]
  return [
    { key: 'arch', label: '模块', value: b.architecture_map?.module_count ?? '-' },
    { key: 'api', label: '路由', value: b.api_spec?.route_count ?? '-' },
    { key: 'ui', label: '视图', value: b.ui_flow?.route_count ?? '-' },
  ]
})

const stageClass = (i) => {
  if (i < stageIndex.value) return 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/60'
  if (i === stageIndex.value) return 'bg-indigo-900/60 text-indigo-200 border border-indigo-600'
  return 'bg-[#2a2a30] text-gray-600 border border-transparent'
}

const severityClass = (s) => {
  if (s === 'CRITICAL') return 'bg-red-900/60 text-red-300'
  if (s === 'HIGH') return 'bg-amber-900/60 text-amber-300'
  if (s === 'MEDIUM') return 'bg-sky-900/60 text-sky-300'
  return 'bg-slate-800 text-slate-400'
}

let pollTimer = null

async function submitTask() {
  if (!prompt.value.trim()) return toast.warning('请输入思考指令')
  running.value = true
  report.value = null
  progressPct.value = 0
  stageIndex.value = 0
  try {
    const res = await api.deepThink.submit(prompt.value.trim(), bindProjectId.value || null)
    currentTaskId.value = res.data?.data?.task_id
    toast.success('长思考任务已受理，开始五阶段流水线')
    startPolling()
  } catch (e) {
    running.value = false
    toast.error('提交失败：' + (e.response?.data?.detail || e.message))
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const res = await api.deepThink.report(currentTaskId.value)
      if (res.data?.data) {
        report.value = res.data.data
        running.value = false
        progressPct.value = 100
        stageIndex.value = 4
        progressMessage.value = '分析报告已生成'
        stopPolling()
        toast.success('长思考分析完成')
        return
      }
    } catch (e) {
      if (e.response?.status === 404) {
        // 任务进行中：读检查点更新进度
        try {
          const cpRes = await api.deepThink.checkpoint(currentTaskId.value)
          const cp = cpRes.data?.data
          if (cp) {
            stageIndex.value = (cp.stage_index ?? 0)
            progressPct.value = ((cp.stage_index ?? 0) + 1) / 5 * 100
            progressMessage.value = `${stages[stageIndex.value]?.label || ''} ${cp.completed ? '已完成' : '进行中...'}`
            if (cp.completed) {
              const rRes = await api.deepThink.report(currentTaskId.value)
              report.value = rRes.data?.data || null
              running.value = false
              stopPolling()
            }
          }
        } catch (_) { /* 无检查点，继续等待 */ }
      }
    }
  }, 1500)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadBlueprint() {
  try {
    const res = await api.deepThink.blueprint()
    blueprint.value = res.data?.data || null
  } catch (e) {
    toast.error('镜像加载失败：' + (e.response?.data?.detail || e.message))
  }
}

async function loadTickets() {
  try {
    const res = await api.deepThink.tickets()
    tickets.value = res.data?.data || []
    toast.success(`生成 ${tickets.value.length} 条进化建议`)
  } catch (e) {
    toast.error('分析失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(async () => {
  try {
    const res = await api.projects.list()
    projects.value = res.data || []
  } catch (_) {}
  loadBlueprint()
})
onBeforeUnmount(stopPolling)
</script>
