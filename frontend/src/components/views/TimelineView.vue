<template>
  <div class="h-full w-full flex flex-col bg-[#1e1e24] text-gray-200 overflow-hidden">
    <!-- 顶栏 -->
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <div class="flex items-center justify-between gap-4 flex-wrap">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold text-gray-100">非线性叙事时间轴</h2>
          <select
            v-model="currentProjectId"
            class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none"
            @change="loadTimelines"
          >
            <option value="" disabled>选择项目...</option>
            <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
          </select>
          <select
            v-model="currentTimelineId"
            class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none"
            @change="loadEvents"
          >
            <option value="" disabled>选择时间线...</option>
            <option v-for="t in timelines" :key="t.timeline_id" :value="t.timeline_id">{{ t.name }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors" @click="openNewTimeline">+ 新时间线</button>
          <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors" @click="addEvent">+ 新事件</button>
          <button
            class="text-xs px-3 py-1.5 rounded border transition-colors"
            :class="conflicts.length ? 'bg-amber-950/60 text-amber-400 border-amber-900/60 hover:bg-amber-900/60' : 'bg-[#2a2a30] text-gray-500 border-gray-700'"
            @click="checkConflicts"
          >冲突检测 ({{ conflicts.length }})</button>
          <button class="text-xs bg-red-950/60 hover:bg-red-900 text-red-400 px-3 py-1.5 rounded border border-red-900/60 transition-colors" @click="removeTimeline">删除时间线</button>
        </div>
      </div>
    </div>

    <!-- 主体：SVG 时间轴 -->
    <div class="flex-1 overflow-hidden relative" v-if="currentTimeline">
      <svg
        ref="svgRef"
        class="w-full h-full block select-none"
        @pointerdown="onCanvasPointerDown"
        @pointermove="onDragMove"
        @pointerup="onDragEnd"
        @pointerleave="onDragEnd"
      >
        <!-- 背景网格 -->
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#2a2a30" stroke-width="0.5"/>
          </pattern>
        </defs>
        <rect :width="viewW" :height="viewH" fill="url(#grid)"/>
        <!-- 轨道 -->
        <g v-for="(track, ti) in currentTimeline.tracks" :key="ti">
          <line :x1="padL" :y1="trackY(ti)" :x2="viewW - padR" :y2="trackY(ti)" :stroke="trackColor(ti)" stroke-width="2" stroke-opacity="0.35"/>
          <text :x="8" :y="trackY(ti) - 10" fill="#94a3b8" font-size="11">{{ track }}</text>
        </g>
        <!-- 事件节点 -->
        <g v-for="ev in currentTimeline.events" :key="ev.event_id">
          <circle
            :cx="eventX(ev)" :cy="trackY(ev.track_index)" r="9"
            :fill="nodeColor(ev)" stroke="#e2e8f0" stroke-width="1.5"
            class="cursor-grab"
            :class="{ 'active: cursor-grabbing': true }"
            @pointerdown.stop="startDrag($event, ev)"
            @dblclick.stop="editEvent(ev)"
          >
            <title>{{ ev.title }}</title>
          </circle>
          <text
            :x="eventX(ev) + 14" :y="trackY(ev.track_index) + 4"
            fill="#e2e8f0" font-size="11" class="pointer-events-none"
          >{{ ev.title }}</text>
        </g>
      </svg>

      <!-- 冲突报告浮层 -->
      <div v-if="showConflicts" class="absolute right-4 top-4 w-80 max-h-96 overflow-y-auto bg-[#1c1c20] border border-amber-900/60 rounded-lg shadow-2xl z-20">
        <div class="px-4 py-3 border-b border-[#2a2a30] flex items-center justify-between">
          <span class="text-sm font-bold text-amber-400">冲突报告</span>
          <button class="text-gray-500 hover:text-white text-xs" @click="showConflicts = false">✕</button>
        </div>
        <div v-if="conflicts.length === 0" class="px-4 py-3 text-xs text-gray-500">未发现冲突 ✓</div>
        <div v-for="c in conflicts" :key="c.conflict_id" class="px-4 py-2.5 border-b border-[#2a2a30] last:border-0">
          <div class="flex items-center gap-2 mb-1">
            <span class="px-1.5 py-0.5 rounded text-[10px]" :class="c.severity === 'ERROR' ? 'bg-red-900/50 text-red-400' : 'bg-amber-900/50 text-amber-400'">{{ c.severity }}</span>
            <span class="text-[10px] text-gray-500 font-mono">{{ c.conflict_type }}</span>
          </div>
          <p class="text-xs text-gray-300 leading-relaxed">{{ c.message }}</p>
        </div>
      </div>
    </div>
    <div v-else class="flex-1 flex items-center justify-center text-gray-600 text-sm">选择项目与时间线后开始</div>

    <!-- 事件编辑弹窗 -->
    <div v-if="editingEvent" class="fixed inset-0 bg-black/50 flex items-center justify-center z-40" @click.self="editingEvent = null">
      <div class="bg-[#1c1c20] border border-gray-700 rounded-lg w-96 p-4">
        <h3 class="text-sm font-bold text-gray-100 mb-3">编辑事件</h3>
        <label class="block text-[10px] text-gray-500 mb-1">标题</label>
        <input v-model="eventForm.title" type="text" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none mb-2"/>
        <label class="block text-[10px] text-gray-500 mb-1">描述</label>
        <textarea v-model="eventForm.description" rows="2" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none mb-2"></textarea>
        <label class="block text-[10px] text-gray-500 mb-1">叙事时间点（如 第3章）</label>
        <input v-model="eventForm.story_time" type="text" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none mb-2"/>
        <div class="flex gap-2 mb-2">
          <div class="flex-1">
            <label class="block text-[10px] text-gray-500 mb-1">轨道</label>
            <select v-model.number="eventForm.track_index" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none">
              <option v-for="(t, ti) in currentTimeline.tracks" :key="ti" :value="ti">轨道 {{ ti + 1 }} · {{ t }}</option>
            </select>
          </div>
          <div class="flex-1">
            <label class="block text-[10px] text-gray-500 mb-1">类型</label>
            <select v-model="eventForm.event_type" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none">
              <option v-for="t in ['PLOT','DEATH','BIRTH','MEETING','BATTLE','REVEAL','OTHER']" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
        </div>
        <label class="block text-[10px] text-gray-500 mb-1">人物状态（JSON，如 {"主角":"存活"}）</label>
        <input v-model="eventForm.charStates" type="text" class="w-full bg-[#2a2a30] border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none mb-3" placeholder='{"主角":"存活"}'/>
        <div class="flex justify-end gap-2">
          <button v-if="editingEvent.event_id" class="text-xs bg-red-950/60 hover:bg-red-900 text-red-400 px-3 py-1.5 rounded border border-red-900/60" @click="deleteEvent">删除</button>
          <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700" @click="editingEvent = null">取消</button>
          <button class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded" @click="saveEvent">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { toast } from '../../utils/toast'
import { api } from '../../api'

const projects = ref([])
const timelines = ref([])
const currentProjectId = ref('')
const currentTimelineId = ref('')
const currentTimeline = ref(null)
const conflicts = ref([])
const showConflicts = ref(false)
const editingEvent = ref(null)
const eventForm = ref({})

// SVG 视图几何
const svgRef = ref(null)
const viewW = ref(1200)
const viewH = ref(600)
const padL = 70
const padR = 30
const trackGap = 90
const trackTop = 60

const trackY = (ti) => trackTop + ti * trackGap
const eventX = (ev) => padL + (ev.position_x / 100) * (viewW.value - padL - padR)
const trackColor = (ti) => ['#818cf8', '#34d399', '#f472b6', '#fbbf24', '#38bdf8', '#fb7185'][ti % 6]
const nodeColor = (ev) => {
  const colors = { DEATH: '#ef4444', BIRTH: '#34d399', BATTLE: '#f97316', REVEAL: '#a78bfa', MEETING: '#38bdf8', PLOT: '#e2e8f0', OTHER: '#94a3b8' }
  return colors[ev.event_type] || '#e2e8f0'
}

// 拖拽状态
const dragState = ref(null)

function onResize() {
  if (svgRef.value) {
    viewW.value = svgRef.value.clientWidth || 1200
    viewH.value = svgRef.value.clientHeight || 600
  }
}

async function loadProjects() {
  const res = await api.projects.list()
  projects.value = res.data || []
}

async function loadTimelines() {
  if (!currentProjectId.value) return
  const res = await api.timeline.list(currentProjectId.value)
  // 后端时间线列表返回信封 { success, data: [...] }，需取内层 data
  timelines.value = res.data?.data || []
  if (timelines.value.length) {
    currentTimelineId.value = timelines.value[0].timeline_id
    await loadEvents()
  } else {
    currentTimeline.value = null
  }
}

async function loadEvents() {
  if (!currentProjectId.value || !currentTimelineId.value) {
    currentTimeline.value = null
    conflicts.value = []
    showConflicts.value = false
    return
  }
  // 阶段A：时间线数据来自 timeline.list（每个 timeline 内嵌 events），
  // 不再从 projects 对象里找（项目列表不含 timelines）。
  try {
    const res = await api.timeline.list(currentProjectId.value)
    const tls = res.data?.data || []
    currentTimeline.value = tls.find(tl => tl.timeline_id === currentTimelineId.value) || null
  } catch {
    currentTimeline.value = null
  }
  conflicts.value = []
  showConflicts.value = false
}

async function openNewTimeline() {
  if (!currentProjectId.value) return toast.warning('请先选择项目')
  const name = await toast.prompt('时间线名称')
  if (!name) return
  const tracks = await toast.prompt('轨道名称（逗号分隔）', '主线,支线')
  const timeline = {
    timeline_id: `tl_${Date.now().toString(36)}`,
    name,
    tracks: (tracks || '主线').split(',').map(s => s.trim()).filter(Boolean),
    events: [],
  }
  const res = await api.timeline.create(currentProjectId.value, timeline)
  if (res.data?.success) {
    await loadTimelines()
    toast.success('时间线已创建')
  }
}

async function addEvent() {
  if (!currentTimeline.value) return toast.warning('请先选择时间线')
  editingEvent.value = { event_id: null, title: '新事件', description: '', story_time: '', track_index: 0, event_type: 'PLOT', position_x: 10, character_states: {} }
  eventForm.value = { ...editingEvent.value, charStates: '{}' }
}

function editEvent(ev) {
  editingEvent.value = ev
  eventForm.value = { ...ev, charStates: JSON.stringify(ev.character_states || {}) }
}

async function saveEvent() {
  try {
    const payload = {
      title: eventForm.value.title || '未命名',
      description: eventForm.value.description || '',
      story_time: eventForm.value.story_time || null,
      track_index: eventForm.value.track_index || 0,
      event_type: eventForm.value.event_type || 'PLOT',
      position_x: Math.min(100, Math.max(0, eventForm.value.position_x ?? 10)),
      character_states: JSON.parse(eventForm.value.charStates || '{}'),
    }
    if (editingEvent.value.event_id) {
      await api.timeline.updateEvent(currentProjectId.value, currentTimelineId.value, editingEvent.value.event_id, payload)
      toast.success('事件已更新')
    } else {
      payload.event_id = `evt_${Date.now().toString(36)}`
      await api.timeline.addEvent(currentProjectId.value, currentTimelineId.value, payload)
      toast.success('事件已添加')
    }
    editingEvent.value = null
    await loadEvents()
  } catch (e) {
    toast.error('保存失败：' + (e.response?.data?.detail || '人物状态 JSON 非法'))
  }
}

async function deleteEvent() {
  if (!editingEvent.value?.event_id) return
  await api.timeline.removeEvent(currentProjectId.value, currentTimelineId.value, editingEvent.value.event_id)
  editingEvent.value = null
  await loadEvents()
  toast.success('事件已删除')
}

async function checkConflicts() {
  if (!currentTimeline.value) return
  const res = await api.timeline.conflicts(currentProjectId.value, currentTimelineId.value)
  conflicts.value = res.data?.data?.conflicts || []
  showConflicts.value = true
}

async function removeTimeline() {
  if (!currentTimelineId.value) return
  const ok = await toast.confirm('确认删除该时间线？')
  if (!ok) return
  await api.timeline.remove(currentProjectId.value, currentTimelineId.value)
  await loadTimelines()
  toast.success('时间线已删除')
}

// ── 拖拽交互 ─────────────────────────────────────────────────
function startDrag(e, ev) {
  dragState.value = { event: ev, offsetX: e.offsetX, pointerId: e.pointerId }
  e.currentTarget.setPointerCapture(e.pointerId)
}

function onDragMove(e) {
  if (!dragState.value || !svgRef.value) return
  const rect = svgRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left
  const usable = viewW.value - padL - padR
  const pct = Math.min(100, Math.max(0, ((x - padL) / usable) * 100))
  dragState.value.event.position_x = Math.round(pct * 10) / 10
}

function onDragEnd() {
  if (dragState.value) {
    const ev = dragState.value.event
    api.timeline.updateEvent(currentProjectId.value, currentTimelineId.value, ev.event_id, ev).catch(() => {})
    dragState.value = null
  }
}

function onCanvasPointerDown() {
  // 点击空白关闭冲突面板
  showConflicts.value = false
}

onMounted(async () => {
  await loadProjects()
  onResize()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
</script>
