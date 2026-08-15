<template>
  <div class="p-4 bg-[#18181b] rounded-lg border border-gray-800">
    <div class="flex justify-between items-center mb-4">
      <h3 class="text-sm font-bold text-gray-300">💾 系统存储与健康状态</h3>
      <button
        @click="triggerGC"
        :disabled="isCleaning"
        class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3 py-1.5 rounded transition-colors disabled:opacity-50 flex items-center space-x-1"
      >
        <svg v-if="isCleaning" class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
        <span>{{ isCleaning ? '垃圾回收中...' : '手动 GC 清理空间' }}</span>
      </button>
    </div>

    <!-- 磁盘剩余可视化条 -->
    <div class="mb-4">
      <div class="flex justify-between text-xs text-gray-400 mb-1">
        <span>系统盘剩余 (Free / Total)</span>
        <span :class="diskFreeGb < 5 ? 'text-red-400 font-bold' : 'text-emerald-400'">
          {{ diskFreeGb }} / {{ stats.disk_total_gb ?? 0 }} GB
        </span>
      </div>
      <div class="w-full bg-gray-700 rounded-full h-2">
        <div
          class="h-2 rounded-full transition-all"
          :class="diskFreeGb < 5 ? 'bg-red-500' : 'bg-gray-400'"
          :style="{ width: `${diskFreePercent}%` }"
        ></div>
      </div>
      <div v-if="diskFreeGb < 5" class="text-[10px] text-red-500 mt-1">
        ⚠️ 磁盘空间极低，写密集型任务已被中枢熔断！
      </div>
    </div>

    <!-- 各目录详细占用（临时/书库/项目） -->
    <div class="grid grid-cols-3 gap-2 text-xs text-gray-400 mb-2">
      <div class="bg-[#27272a] p-2 rounded">
        <div class="font-bold text-gray-300 mb-1">临时文件 Temp</div>
        <div class="text-blue-400">{{ dirUsage.temp || 0 }} MB</div>
      </div>
      <div class="bg-[#27272a] p-2 rounded">
        <div class="font-bold text-gray-300 mb-1">书库 Library</div>
        <div class="text-amber-400">{{ dirUsage.library || 0 }} MB</div>
      </div>
      <div class="bg-[#27272a] p-2 rounded">
        <div class="font-bold text-gray-300 mb-1">项目 Projects</div>
        <div class="text-purple-400">{{ dirUsage.projects || 0 }} MB</div>
      </div>
    </div>

    <!-- GC 执行进度/报告 -->
    <div v-if="gcReport" class="mt-3 bg-[#1f1f22] border border-gray-700 rounded p-2 text-[11px] text-gray-400">
      <div class="font-bold text-gray-300 mb-1">GC 报告 ({{ gcReport.status }})</div>
      <div>清理文件: {{ gcReport.temp_files_removed || 0 }} 个</div>
      <div>
        数据库压缩:
        <span v-if="gcReport.vacuum_done" class="text-emerald-400">已完成 VACUUM</span>
        <span v-else-if="gcReport.vacuum_skipped_reason" class="text-yellow-400">{{ gcReport.vacuum_skipped_reason }}</span>
        <span v-else class="text-gray-500">等待系统空闲...</span>
      </div>
    </div>
  </div>

  <!-- 水位告警弹窗 -->
  <Teleport to="body">
    <div v-if="showWarning" class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60" @click.self="showWarning = false">
      <div class="bg-[#1e1e24] border border-red-800 rounded-xl p-5 w-96 shadow-2xl">
        <div class="flex items-center space-x-2 mb-3">
          <span class="text-2xl">⚠️</span>
          <h3 class="text-base font-bold text-red-400">存储水位告警</h3>
        </div>
        <div class="space-y-1.5 text-xs text-gray-300">
          <div class="flex justify-between">
            <span>系统盘剩余</span>
            <span class="text-red-400 font-bold">{{ diskFreeGb }} GB</span>
          </div>
          <div class="flex justify-between">
            <span>磁盘占用率</span>
            <span class="text-red-400">{{ stats.disk_percent ?? 0 }}%</span>
          </div>
          <div class="flex justify-between">
            <span>临时文件占用</span>
            <span class="text-amber-400">{{ dirUsage.temp || 0 }} MB</span>
          </div>
        </div>
        <div class="text-[11px] text-gray-500 mt-3 leading-relaxed">
          磁盘空间不足可能影响写密集型任务（量化/发散引擎）。建议点击「手动 GC」清理临时文件，或迁移数据释放空间。
        </div>
        <div class="mt-4 flex justify-end space-x-2">
          <button @click="showWarning = false" class="text-xs px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition-colors">知道了</button>
          <button @click="warnAndGC" class="text-xs px-3 py-1.5 bg-red-700 hover:bg-red-600 text-white rounded transition-colors">立即清理</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
import { useVisibilityPolling } from '../composables/useVisibilityPolling'

const stats = ref({ disk_free_gb: 0, disk_total_gb: 0, disk_percent: 0, directory_usage_mb: {} })
const dirUsage = computed(() => stats.value.directory_usage_mb || {})
const isCleaning = ref(false)
const gcReport = ref(null)
const showWarning = ref(false)
const warnedOnce = ref(false)
let gcPollTimer = null

const diskFreeGb = computed(() => Number(stats.value.disk_free_gb || 0))
const diskFreePercent = computed(() => {
  const total = Number(stats.value.disk_total_gb || 0)
  if (!total) return 0
  return Math.max(0, Math.min(100, (diskFreeGb.value / total) * 100))
})

const checkWaterLevel = () => {
  // 水位告警：磁盘剩余 < 5GB 或占用率 > 90%
  const lowDisk = diskFreeGb.value > 0 && diskFreeGb.value < 5
  const highUsage = Number(stats.value.disk_percent || 0) > 90
  if ((lowDisk || highUsage) && !warnedOnce.value) {
    showWarning.value = true
    warnedOnce.value = true
  }
}

const fetchStats = async () => {
  try {
    const res = await api.system.resourceStats()
    stats.value = res.data.data
    checkWaterLevel()
  } catch (error) {
    console.error('无法获取系统状态', error)
  }
}

const pollGC = async (taskId) => {
  clearInterval(gcPollTimer)
  gcPollTimer = setInterval(async () => {
    try {
      const res = await api.system.gcReport(taskId)
      gcReport.value = res.data.data
      if (['COMPLETED', 'FAILED'].includes(gcReport.value.status)) {
        clearInterval(gcPollTimer)
        isCleaning.value = false
        fetchStats()
      }
    } catch (e) {
      clearInterval(gcPollTimer)
      isCleaning.value = false
    }
  }, 1500)
}

const triggerGC = async () => {
  isCleaning.value = true
  gcReport.value = null
  try {
    const res = await api.system.triggerGC()
    const taskId = res.data.data?.task_id
    if (taskId) {
      pollGC(taskId)
    } else {
      isCleaning.value = false
    }
  } catch (error) {
    console.error(error)
    isCleaning.value = false
  }
}

const warnAndGC = () => {
  showWarning.value = false
  triggerGC()
}

// 页面可见才轮询；卸载自动清理
useVisibilityPolling(fetchStats, 10000)
onMounted(fetchStats)
onUnmounted(() => { if (gcPollTimer) clearInterval(gcPollTimer) })
</script>
