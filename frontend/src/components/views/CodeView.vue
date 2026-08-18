<template>
  <div class="h-full flex flex-col bg-[#121212] text-gray-200">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30]">
      <h2 class="text-sm font-semibold">编程副驾驶 · L2 受限验证</h2>
      <p class="text-[10px] text-gray-500 mt-1">仅支持 Python 编译/测试；无 shell、无网络、无正式工作区写入。</p>
    </div>
    <div class="px-6 py-4 space-y-3 max-w-3xl">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <label class="text-xs text-gray-400">验证命令
          <select v-model="command" class="mt-1 w-full bg-[#18181b] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-gray-200">
            <option value="compileall">Python 编译检查</option>
            <option value="pytest">运行 pytest</option>
            <option value="unittest">运行 unittest</option>
          </select>
        </label>
        <label class="text-xs text-gray-400">资源档位
          <select v-model="resourceProfile" class="mt-1 w-full bg-[#18181b] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-gray-200">
            <option value="auto">自动保守分配</option>
            <option value="low">低负载</option>
            <option value="standard">标准：最多 6 核 / 8 GiB / 20 GiB</option>
            <option value="high">高负载：最多 8 核 / 16 GiB / 40 GiB</option>
          </select>
        </label>
        <label class="text-xs text-gray-400">工作区相对目标
          <input v-model="target" class="mt-1 w-full bg-[#18181b] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-gray-200" placeholder="例如 tests/test_code_execution.py">
        </label>
      </div>
      <div class="flex gap-2">
        <button :disabled="loading" class="text-xs bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded px-3 py-1.5" @click="makePlan">生成计划</button>
        <button :disabled="loading" class="text-xs bg-[#2a2a30] hover:bg-gray-600 disabled:opacity-50 text-gray-300 rounded px-3 py-1.5" @click="loadSnapshot">查看快照</button>
        <button :disabled="loading" class="text-xs bg-amber-700/70 hover:bg-amber-600 disabled:opacity-50 text-white rounded px-3 py-1.5" @click="stageSample">演示 staging</button>
        <button v-if="plan" :disabled="loading" class="text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white rounded px-3 py-1.5" @click="runPlan">批准并执行</button>
      </div>
      <div v-if="snapshot" class="border border-[#2a2a30] bg-[#18181b] rounded-lg p-3 text-xs">
        <div class="text-gray-300 mb-2">工作区快照 {{ snapshot.snapshot_id }}</div>
        <pre class="text-gray-500 whitespace-pre-wrap max-h-48 overflow-auto">{{ JSON.stringify(snapshot.files, null, 2) }}</pre>
      </div>
      <div v-if="staged" class="border border-amber-900/50 bg-[#18181b] rounded-lg p-3 text-xs">
        <div class="text-amber-300 mb-2">staging：{{ staged.path }} · {{ staged.status }}</div>
        <div class="text-[10px] text-gray-500">基线 {{ staged.base_digest }} → 新版本 {{ staged.after_digest }}</div>
        <div class="flex gap-2 mt-2">
          <button v-if="staged.status === 'STAGED'" class="text-xs bg-emerald-700 hover:bg-emerald-600 text-white rounded px-2 py-1" @click="promoteStaged">批准并应用</button>
          <button v-if="staged.status === 'PROMOTED'" class="text-xs bg-red-900/60 hover:bg-red-800 text-red-200 rounded px-2 py-1" @click="rollbackStaged">回滚</button>
        </div>
      </div>
      <div v-if="plan" class="border border-indigo-900/50 bg-[#18181b] rounded-lg p-3 text-xs">
        <div class="text-indigo-300 mb-2">计划 {{ plan.plan_id }} · 风险 {{ plan.risk_level }}</div>
        <pre class="text-gray-400 whitespace-pre-wrap">{{ JSON.stringify(plan, null, 2) }}</pre>
      </div>
      <div v-if="taskState" class="border border-cyan-900/50 bg-[#18181b] rounded-lg p-3 text-xs">
        <div class="flex items-center justify-between"><span class="text-cyan-300">任务 {{ taskState.task_id }}</span><span>{{ taskState.phase }} · {{ taskState.progress }}%</span></div>
        <div class="text-gray-500 mt-1">{{ taskState.message }}</div>
        <button v-if="['RUNNING','PENDING'].includes(taskState.status)" class="mt-2 text-xs bg-red-900/60 text-red-200 rounded px-2 py-1" @click="cancelTask">取消任务</button>
      </div>
      <div v-if="result" class="border border-[#2a2a30] bg-[#18181b] rounded-lg p-3 text-xs">
        <div :class="result.exit_code === 0 ? 'text-emerald-400' : 'text-red-400'">退出码：{{ result.exit_code }}</div>
        <div class="text-gray-500 mt-2">stdout</div><pre class="text-gray-300 whitespace-pre-wrap max-h-64 overflow-auto">{{ result.stdout }}</pre>
        <div v-if="result.stderr" class="text-gray-500 mt-2">stderr</div><pre v-if="result.stderr" class="text-red-300 whitespace-pre-wrap max-h-64 overflow-auto">{{ result.stderr }}</pre>
      </div>
      <div v-if="error" class="text-xs text-red-400">{{ error }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { api } from '../../api'

const command = ref('compileall')
const target = ref('.')
const resourceProfile = ref('auto')
const plan = ref<any>(null)
const result = ref<any>(null)
const snapshot = ref<any>(null)
const staged = ref<any>(null)
const error = ref('')
const taskState = ref<any>(null)
const loading = ref(false)
const pollTimer = ref<any>(null)

const loadSnapshot = async () => {
  loading.value = true; error.value = ''
  try {
    const response = await api.code.snapshot(target.value || '.')
    snapshot.value = response.data?.data || null
  } catch (e: any) { error.value = e?.response?.data?.detail || '读取工作区快照失败' }
  finally { loading.value = false }
}

const stageSample = async () => {
  loading.value = true; error.value = ''
  try {
    const response = await api.code.stage({ path: target.value || 'code_staging_sample.py', content: '# staging sample\nvalue = 1\n' })
    staged.value = response.data?.data || null
  } catch (e: any) { error.value = e?.response?.data?.detail || '进入 staging 失败' }
  finally { loading.value = false }
}

const promoteStaged = async () => {
  if (!staged.value) return
  loading.value = true
  try {
    const response = await api.code.promote(staged.value.change_id, `author-${Date.now()}`)
    staged.value = response.data?.data || staged.value
  } catch (e: any) { error.value = e?.response?.data?.detail || '应用 staging 变更失败' }
  finally { loading.value = false }
}

const rollbackStaged = async () => {
  if (!staged.value) return
  loading.value = true
  try {
    const response = await api.code.rollback(staged.value.change_id, `author-rollback-${Date.now()}`)
    staged.value = response.data?.data || staged.value
  } catch (e: any) { error.value = e?.response?.data?.detail || '回滚 staging 变更失败' }
  finally { loading.value = false }
}

const makePlan = async () => {
  loading.value = true; error.value = ''; result.value = null
  try {
    const response = await api.code.plan({ command: command.value, target: target.value, workspace_id: 'default', resource_profile: resourceProfile.value })
    plan.value = response.data?.data || null
  } catch (e: any) { error.value = e?.response?.data?.detail || '生成代码计划失败' }
  finally { loading.value = false }
}

const cancelTask = async () => {
  if (!taskState.value?.task_id) return
  try {
    const response = await api.code.cancel(taskState.value.task_id)
    taskState.value = response.data?.data || taskState.value
  } catch (e: any) { error.value = e?.response?.data?.detail || '取消任务失败' }
}

const runPlan = async () => {
  if (!plan.value) return
  loading.value = true; error.value = ''
  try {
    const response = await api.code.run({
      request: { command: command.value, target: target.value, workspace_id: 'default', resource_profile: resourceProfile.value },
      plan_id: plan.value.plan_id,
      approval_id: `author-${Date.now()}`,
    })
    result.value = response.data?.data || null
    if (result.value?.task_id) {
      taskState.value = { task_id: result.value.task_id, status: 'PENDING', phase: 'PLANNING', progress: 0, message: '代码任务已进入队列' }
      if (pollTimer.value) clearInterval(pollTimer.value)
      pollTimer.value = setInterval(async () => {
        try {
          const state = await api.code.state(result.value.task_id)
          taskState.value = state.data?.data || taskState.value
          if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(taskState.value.status) && pollTimer.value) {
            clearInterval(pollTimer.value); pollTimer.value = null
          }
        } catch { /* 状态查询失败保留当前 UI */ }
      }, 1000)
    }
  } catch (e: any) { error.value = e?.response?.data?.detail || '代码验证失败' }
  finally { loading.value = false }
}
onUnmounted(() => {
  if (pollTimer.value) clearInterval(pollTimer.value)
})
</script>
