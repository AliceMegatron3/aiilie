<template>
  <div class="space-y-4 p-4">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">九阶情感量化引擎</h2>
      <div class="flex items-center space-x-2">
        <input
          v-model="batchBookId"
          class="bg-[#101014] border border-gray-700 rounded px-2 py-1 text-[11px] font-mono text-gray-300 w-40 focus:border-indigo-500 outline-none"
          placeholder="book_id"
        />
        <button
          @click="startBatch"
          :disabled="batchRunning"
          class="text-[11px] px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-50"
        >{{ batchRunning ? '批量量化中...' : '批量量化书籍' }}</button>
        <button @click="loadFrames" class="text-xs text-gray-400 hover:text-gray-200 transition-colors">刷新</button>
      </div>
    </div>

    <!-- 批量任务进度条 -->
    <div v-if="batchTaskId" class="bg-[#18181b] border border-gray-800 rounded-lg p-3">
      <div class="flex justify-between text-[11px] text-gray-400 mb-1.5">
        <span class="font-mono">任务 {{ batchTaskId.substring(0, 12) }}...</span>
        <span :class="batchStatus === 'COMPLETED' ? 'text-emerald-400' : 'text-indigo-400'">
          {{ batchStatus }} {{ batchCompleted }}/{{ batchTotal }} 段
        </span>
      </div>
      <div class="w-full bg-gray-700 rounded-full h-2">
        <div
          class="h-2 rounded-full transition-all"
          :class="batchStatus === 'COMPLETED' ? 'bg-emerald-500' : 'bg-indigo-500'"
          :style="{ width: `${batchPercent}%` }"
        ></div>
      </div>
    </div>

    <!-- 帧列表 -->
    <div v-if="isLoading" class="text-xs text-gray-500 text-center py-4">加载帧数据...</div>
    <div v-else-if="frames.length === 0" class="text-xs text-gray-600 text-center py-8">
      暂无情感帧。可通过单段量化或整书批量量化生成。
    </div>
    <div v-else class="space-y-2">
      <div
        v-for="f in frames"
        :key="f.frame_id"
        class="bg-[#18181b] border border-gray-800 rounded-lg p-3 cursor-pointer hover:border-indigo-500 transition-colors"
        :class="{ 'border-indigo-500': selectedFrameId === f.frame_id }"
        @click="selectFrame(f.frame_id)"
      >
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center space-x-2">
            <span class="font-mono text-[11px] text-gray-400">{{ f.frame_id.substring(0, 10) }}...</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-400">{{ f.frame_type }}</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-400">{{ f.mode }}</span>
          </div>
          <span class="text-[11px] font-bold" :class="waveColor(f.wave_level)">wave {{ f.wave_level }}</span>
        </div>
        <!-- 波形条 -->
        <div class="flex items-center gap-1">
          <div
            v-for="i in 9"
            :key="i"
            class="h-1.5 flex-1 rounded"
            :class="i <= f.wave_level ? 'bg-gradient-to-r from-blue-500 to-purple-500' : 'bg-gray-800'"
          ></div>
        </div>
      </div>
    </div>

    <!-- 帧详情：三路情绪 -->
    <div v-if="frameDetail" class="mt-4 bg-[#121216] border border-gray-800 rounded-lg p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-bold text-gray-200">帧详情</h3>
        <div class="flex items-center space-x-2">
          <span class="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-400">wave_level {{ frameDetail.wave_level }}</span>
          <span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-400">mode {{ frameDetail.mode }}</span>
        </div>
      </div>
      <div class="grid grid-cols-3 gap-3 mb-3">
        <div class="bg-[#1a1a20] rounded-lg p-3 border border-gray-800">
          <div class="text-[11px] text-gray-500 mb-1.5">场景情绪 scene</div>
          <pre class="text-[11px] text-sky-300 whitespace-pre-wrap break-all">{{ fmtJson(frameDetail.scene_emotion) }}</pre>
        </div>
        <div class="bg-[#1a1a20] rounded-lg p-3 border border-gray-800">
          <div class="text-[11px] text-gray-500 mb-1.5">人物内心 inner</div>
          <pre class="text-[11px] text-rose-300 whitespace-pre-wrap break-all">{{ fmtJson(frameDetail.character_inner_emotion) }}</pre>
        </div>
        <div class="bg-[#1a1a20] rounded-lg p-3 border border-gray-800">
          <div class="text-[11px] text-gray-500 mb-1.5">冲突情绪 conflict</div>
          <pre class="text-[11px] text-amber-300 whitespace-pre-wrap break-all">{{ fmtJson(frameDetail.conflict_emotion) }}</pre>
        </div>
      </div>
      <div class="text-[11px] text-gray-500 mb-1">原文片段</div>
      <div class="bg-[#0c0c10] border border-gray-800 rounded-lg p-2.5 text-[11px] text-gray-400 line-clamp-3">{{ frameDetail.text_snippet || '（空）' }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../../api'
import { useVisibilityPolling } from '../../composables/useVisibilityPolling'

const frames = ref([])
const isLoading = ref(false)
const selectedFrameId = ref(null)
const frameDetail = ref(null)
const batchBookId = ref('')
const batchTaskId = ref(null)
const batchStatus = ref('')
const batchCompleted = ref(0)
const batchTotal = ref(0)
const batchRunning = ref(false)
let batchTimer = null

const batchPercent = ref(0)

const waveColor = (level) => {
  if (level >= 7) return 'text-red-400'
  if (level >= 4) return 'text-amber-400'
  return 'text-emerald-400'
}

const fmtJson = (obj) => {
  try {
    return JSON.stringify(obj || {}, null, 1)
  } catch {
    return String(obj ?? '')
  }
}

const loadFrames = async () => {
  isLoading.value = true
  try {
    const res = await api.emotion.frames(null, 50)
    if (res.data.status === 'success') frames.value = res.data.data || []
  } catch (e) {
    console.error('帧列表加载失败', e)
    frames.value = []
  } finally {
    isLoading.value = false
  }
}

const selectFrame = async (frameId) => {
  selectedFrameId.value = frameId
  try {
    const res = await api.emotion.frameDetail(frameId)
    if (res.data.status === 'success') frameDetail.value = res.data.data
  } catch (e) {
    frameDetail.value = null
  }
}

const startBatch = async () => {
  if (!batchBookId.value.trim()) return
  batchRunning.value = true
  batchTaskId.value = null
  try {
    const res = await api.emotion.batchQuantize(batchBookId.value.trim())
    batchTaskId.value = res.data.task_id
    pollBatch()
  } catch (e) {
    console.error('批量量化启动失败', e)
    batchRunning.value = false
  }
}

const pollBatch = () => {
  clearInterval(batchTimer)
  batchTimer = setInterval(async () => {
    if (!batchTaskId.value) return
    try {
      const res = await api.tasks.detail(batchTaskId.value)
      const d = res.data || {}
      batchStatus.value = d.status || ''
      const segs = d.segments || []
      batchTotal.value = segs.length
      batchCompleted.value = segs.filter((s) => s.status === 'COMPLETED').length
      batchPercent.value = batchTotal.value ? Math.round((batchCompleted.value / batchTotal.value) * 100) : 0
      if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(batchStatus.value)) {
        clearInterval(batchTimer)
        batchRunning.value = false
        loadFrames()
      }
    } catch (e) {
      clearInterval(batchTimer)
      batchRunning.value = false
    }
  }, 2000)
}

useVisibilityPolling(loadFrames, 10000)
onMounted(loadFrames)
onUnmounted(() => { if (batchTimer) clearInterval(batchTimer) })
</script>
