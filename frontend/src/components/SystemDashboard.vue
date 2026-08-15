<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
const systemState = ref('IDLE')
const cpuPercent = ref(0)
const memoryPercent = ref(0)
const queueInfo = ref(null)
const resourceStats = ref(null)
const plugins = ref([])
const isGcRunning = ref(false)
// 趋势图采样历史（最多 60 个点）
const MAX_SAMPLES = 60
const cpuHistory = ref([])
const memoryHistory = ref([])
const statusColor = computed(() => {
  if (systemState.value === 'IDLE') return 'text-green-400'
  if (systemState.value === 'OFFLINE') return 'text-red-400'
  return 'text-yellow-400'
})
// 磁盘使用率（%）：后端 /system/resource-stats 返回 disk_percent（已用百分比）
const diskUsagePercent = computed(() => {
  if (!resourceStats.value || typeof resourceStats.value.disk_percent !== 'number') return 0
  return resourceStats.value.disk_percent
})
// 折线坐标映射（viewBox 600x140）
const CHART_W = 600
const CHART_H = 140
const historyToPoints = (history) => {
  if (history.length < 2) return ''
  return history.map((v, i) => {
    const x = (i / (MAX_SAMPLES - 1)) * CHART_W
    const y = CHART_H - (Math.min(v, 100) / 100) * CHART_H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
const cpuLinePoints = computed(() => historyToPoints(cpuHistory.value))
const memoryLinePoints = computed(() => historyToPoints(memoryHistory.value))
const lastPoint = computed(() => {
  const n = cpuHistory.value.length
  if (n === 0) return null
  const x = ((n - 1) / (MAX_SAMPLES - 1)) * CHART_W
  const cpuY = CHART_H - (Math.min(cpuHistory.value[n - 1], 100) / 100) * CHART_H
  const memY = CHART_H - (Math.min(memoryHistory.value[n - 1], 100) / 100) * CHART_H
  return { x, cpuY, memY }
})
// 队列深度条形图：相对 max(排队数, 10) 的比例
const queuePercent = (n) => {
  const denom = Math.max(queueInfo.value?.pending_tasks_count || 0, queueInfo.value?.batch1_engine?.queue_size || 0, 10)
  return Math.min(100, (n / denom) * 100) + '%'
}
const pushSample = (history, value) => {
  history.push(Math.round(value))
  if (history.length > MAX_SAMPLES) history.shift()
}
const formatBytes = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024
    i++
  }
  return bytes.toFixed(1) + ' ' + units[i]
}
// 磁盘显示辅助：后端返回 directory_usage_mb（MB 数值）与 disk_free_gb（GB）
const tempUsageMb = computed(() => resourceStats.value?.directory_usage_mb?.temp ?? 0)
const diskFreeGb = computed(() => resourceStats.value?.disk_free_gb ?? 0)
const loadSystemStatus = async () => {
  try {
    const res = await api.system.status()
    systemState.value = res.data.global_state || 'IDLE'
    const health = res.data.health_report || {}
    cpuPercent.value = Math.round(health.cpu_percent || 0)
    memoryPercent.value = Math.round(health.memory_percent || 0)
    // 趋势采样
    pushSample(cpuHistory.value, cpuPercent.value)
    pushSample(memoryHistory.value, memoryPercent.value)
  } catch (e) {
    systemState.value = 'OFFLINE'
  }
}
const loadQueue = async () => {
  try {
    const res = await api.system.queue()
    queueInfo.value = res.data
  } catch (e) {
    // 静默
  }
}
const loadResourceStats = async () => {
  try {
    const res = await api.system.resourceStats()
    // 后端响应信封为 { success, data: {...} }
    if (res.data.success && res.data.data) {
      resourceStats.value = res.data.data
    }
  } catch (e) {
    // 静默
  }
}
const loadPlugins = async () => {
  try {
    const res = await api.plugins.list()
    plugins.value = res.data.data || []
  } catch (e) {
    plugins.value = []
  }
}
const triggerGC = async () => {
  isGcRunning.value = true
  try {
    await api.system.triggerGC()
    setTimeout(() => loadResourceStats(), 3000)
  } catch (e) {
    console.error('GC 触发失败', e)
  } finally {
    isGcRunning.value = false
  }
}
const installPlugin = async () => {
  try {
    await api.plugins.install()
    loadPlugins()
  } catch (e) {
    console.error('插件安装失败', e)
  }
}
// 可见性感知轮询：页面隐藏时暂停，恢复可见立即刷新
let timers = []
const isVisible = () => !document.hidden
const onVisibilityChange = () => {
  if (isVisible()) {
    loadSystemStatus()
    loadQueue()
  }
}
onMounted(() => {
  loadSystemStatus()
  loadQueue()
  loadResourceStats()
  loadPlugins()
  // 定时刷新（隐藏标签页时跳过请求）
  timers.push(setInterval(() => { if (isVisible()) loadSystemStatus() }, 5000))
  timers.push(setInterval(() => { if (isVisible()) loadQueue() }, 10000))
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onUnmounted(() => {
  timers.forEach((t) => clearInterval(t))
  timers = []
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>