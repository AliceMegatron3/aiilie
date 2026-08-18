<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0 flex items-center gap-3">
      <h2 class="text-sm font-semibold text-gray-200">群像 · 角色时间线状态</h2>
      <select v-model="projectId" class="text-xs bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" @change="loadTracks">
        <option value="" disabled>选择项目</option>
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
      </select>
      <select v-model="selectedChar" class="text-xs bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" @change="loadHistory">
        <option value="" disabled>选择角色</option>
        <option v-for="t in tracks" :key="t.character_id" :value="t.character_id">{{ t.name || t.character_id }}</option>
      </select>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4" v-if="history.length">
      <!-- 按章时间线 -->
      <div class="text-xs text-gray-300 mb-2">角色时间线(按章号)</div>
      <div class="relative pl-4">
        <div v-for="h in history" :key="h.chapter_key" class="border-l-2 border-indigo-500/30 pl-4 pb-3 relative">
          <div class="absolute left-[-5px] top-0 w-2 h-2 rounded-full bg-indigo-500"></div>
          <div class="text-xs text-gray-300">第{{ h.chapter_key }}章 <span v-if="h.story_time" class="text-amber-500/80 ml-1">{{ h.story_time }}</span></div>
          <div class="text-[11px] text-gray-400 mt-0.5">{{ h.position || '—' }}</div>
          <div v-if="h.livelihood" class="text-[10px] text-gray-500">营生:{{ h.livelihood }}</div>
          <div v-if="h.needs?.length" class="text-[10px] text-gray-500">需求:{{ h.needs.join('/') }}</div>
          <div v-if="h.attachments?.length" class="text-[10px] text-gray-600">牵挂:{{ h.attachments.join('/') }}</div>
        </div>
      </div>

      <!-- 指定章号快照查询 -->
      <div class="mt-4 border-t border-[#2a2a30] pt-3">
        <div class="text-xs text-gray-300 mb-1">查询某章号状态</div>
        <div class="flex gap-2 text-xs">
          <input v-model.number="queryChapter" type="number" min="1" placeholder="章号" class="w-24 bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <button class="bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="queryAtChapter">查询</button>
        </div>
        <div v-if="atChapterTrack" class="mt-2 text-[11px] text-gray-400">
          第{{ queryChapter }}章 → 第{{ atChapterTrack.chapter_key }}章状态:<span class="text-gray-200 ml-1">{{ atChapterTrack.position }}</span>
          <span v-if="atChapterTrack.story_time" class="text-amber-500/80 ml-1">({{ atChapterTrack.story_time }})</span>
        </div>
      </div>
    </div>
    <div v-else class="flex-1 flex items-center justify-center text-gray-600 text-sm">
      {{ projectId ? (tracks.length ? '选择角色查看其时间线' : '该项目暂无角色轨道') : '请选择项目' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, unwrap } from '../../api'

const projects = ref<any[]>([])
const projectId = ref('')
const tracks = ref<any[]>([])
const selectedChar = ref('')
const history = ref<any[]>([])
const queryChapter = ref(1)
const atChapterTrack = ref<any>(null)

async function loadProjects() {
  try {
    const res = await api.projects.list()
    projects.value = res.data || []
  } catch { projects.value = [] }
}

async function loadTracks() {
  selectedChar.value = ''
  history.value = []
  if (!projectId.value) return
  try {
    const res = await api.governance.ensembleTracks(projectId.value)
    tracks.value = unwrap<any>(res)?.tracks || []
  } catch { tracks.value = [] }
}

async function loadHistory() {
  if (!selectedChar.value || !projectId.value) return
  try {
    const res = await api.governance.trackHistoryByChar(projectId.value, selectedChar.value)
    history.value = unwrap<any>(res)?.history || []
  } catch { history.value = [] }
}

async function queryAtChapter() {
  if (!selectedChar.value || !projectId.value || !queryChapter.value) return
  try {
    const res = await api.governance.trackAtChapter(projectId.value, selectedChar.value, queryChapter.value)
    atChapterTrack.value = unwrap<any>(res)?.track || null
  } catch { atChapterTrack.value = null }
}

onMounted(loadProjects)
</script>