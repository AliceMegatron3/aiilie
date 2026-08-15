<template>
  <div class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center">
    <div class="bg-[#1e1e24] border border-[#2a2a30] rounded-lg shadow-2xl w-[500px] overflow-hidden flex flex-col">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-[#2a2a30] flex justify-between items-center bg-[#18181b]">
        <h3 class="text-lg font-semibold text-gray-200">系统自检报告</h3>
        <button @click="$emit('close')" class="text-gray-400 hover:text-white">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
        </button>
      </div>

      <!-- Body -->
      <div class="p-6 flex-1 overflow-y-auto space-y-4">
        <!-- Backend Status -->
        <div class="flex items-start gap-3">
          <div :class="`mt-1 w-2 h-2 rounded-full ${backendStatus === 'OK' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : (backendStatus === 'Checking' ? 'bg-gray-500 animate-pulse' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]')}`"></div>
          <div>
            <div class="text-gray-200 font-medium text-sm">后端基础服务连接</div>
            <div class="text-gray-400 text-xs mt-1">Status: {{ backendStatusMsg }}</div>
          </div>
        </div>

        <!-- System State -->
        <div class="flex items-start gap-3">
          <div :class="`mt-1 w-2 h-2 rounded-full ${systemState === 'OK' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : (systemState === 'Checking' ? 'bg-gray-500 animate-pulse' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]')}`"></div>
          <div>
            <div class="text-gray-200 font-medium text-sm">全局调度中枢状态</div>
            <div class="text-gray-400 text-xs mt-1">Status: {{ systemStateMsg }}</div>
          </div>
        </div>

        <!-- Queue Status -->
        <div class="flex items-start gap-3">
          <div :class="`mt-1 w-2 h-2 rounded-full ${queueStatus === 'OK' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : (queueStatus === 'Checking' ? 'bg-gray-500 animate-pulse' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]')}`"></div>
          <div>
            <div class="text-gray-200 font-medium text-sm">任务队列服务</div>
            <div class="text-gray-400 text-xs mt-1">Status: {{ queueStatusMsg }}</div>
          </div>
        </div>

        <!-- Batch1 Engine -->
        <div class="flex items-start gap-3">
          <div :class="`mt-1 w-2 h-2 rounded-full ${batch1Status === 'OK' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : (batch1Status === 'Checking' ? 'bg-gray-500 animate-pulse' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]')}`"></div>
          <div>
            <div class="text-gray-200 font-medium text-sm">Batch1 分段执行引擎</div>
            <div class="text-gray-400 text-xs mt-1">Status: {{ batch1StatusMsg }}</div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 bg-[#18181b] border-t border-[#2a2a30] flex justify-end">
        <button @click="$emit('close')" class="px-4 py-2 bg-[#2a2a30] text-gray-200 rounded hover:bg-[#3a3a40] text-sm font-medium transition-colors">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

defineEmits(['close'])

const backendStatus = ref('Checking')
const backendStatusMsg = ref('正在检测...')
const systemState = ref('Checking')
const systemStateMsg = ref('正在检测...')
const queueStatus = ref('Checking')
const queueStatusMsg = ref('正在检测...')
const batch1Status = ref('Checking')
const batch1StatusMsg = ref('正在检测...')

onMounted(async () => {
  // 1. 检测后端基础连接 + 系统状态
  try {
    const res = await api.system.status()
    if (res.status === 200) {
      backendStatus.value = 'OK'
      backendStatusMsg.value = `OK (HTTP 200)`

      const globalState = res.data.global_state || 'UNKNOWN'
      systemState.value = 'OK'
      systemStateMsg.value = `${globalState} - ${JSON.stringify(res.data.health_report || {}).substring(0, 80)}`
    }
  } catch (error) {
    backendStatus.value = 'Error'
    backendStatusMsg.value = 'Error (无法连接到后端服务器)'
    systemState.value = 'Error'
    systemStateMsg.value = '未检测到服务'
    queueStatus.value = 'Error'
    queueStatusMsg.value = '未检测到服务'
    batch1Status.value = 'Error'
    batch1StatusMsg.value = '未检测到服务'
    return
  }

  // 2. 检测队列服务
  try {
    const res = await api.system.queue()
    queueStatus.value = 'OK'
    queueStatusMsg.value = `排队任务: ${res.data.pending_tasks_count || 0} | 当前执行: ${res.data.active_task_id || 'N/A'}`

    // 3. 检测 Batch1 引擎
    const batch1 = res.data.batch1_engine
    if (batch1 && batch1.mounted) {
      batch1Status.value = 'OK'
      batch1StatusMsg.value = `已挂载 | 队列: ${batch1.queue_size || 0} | 跟踪: ${batch1.tracked_tasks || 0}`
    } else {
      batch1Status.value = 'Error'
      batch1StatusMsg.value = 'Batch1 引擎未挂载'
    }
  } catch (error) {
    queueStatus.value = 'Error'
    queueStatusMsg.value = '队列服务不可用'
    batch1Status.value = 'Error'
    batch1StatusMsg.value = '未检测到服务'
  }
})
</script>
