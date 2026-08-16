<template>
  <div class="h-full flex flex-col">
    <!-- 顶栏 -->
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0 flex items-center gap-3">
      <h2 class="text-sm font-semibold text-gray-200">叙事结构层 · 卷 / 章 / 拍 / 伏笔</h2>
      <select v-model="selectedProjectId" class="text-xs bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" @change="loadVolumes">
        <option value="" disabled>选择项目</option>
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
      </select>
      <button class="text-xs bg-indigo-600/60 hover:bg-indigo-500 text-white rounded px-3 py-1" @click="showNewVol = !showNewVol">新建卷</button>
    </div>

    <!-- 新建卷 -->
    <div v-if="showNewVol" class="px-6 py-2 bg-[#1a1a1f] border-b border-[#2a2a30] flex gap-2 text-xs">
      <input v-model="newVol.title" placeholder="卷名" class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200 flex-1" />
      <button class="bg-emerald-700/70 text-white rounded px-3 py-1" @click="createVolume">创建</button>
    </div>

    <!-- 双栏 -->
    <div class="flex-1 flex overflow-hidden">
      <!-- 左:卷列表+章列表 -->
      <div class="w-1/3 border-r border-[#2a2a30] overflow-y-auto">
        <div v-for="vol in volumes" :key="vol.volume_id" class="cursor-pointer px-4 py-2 border-b border-[#1f1f25]"
             :class="selectedVol === vol.volume_id ? 'bg-indigo-900/30' : 'hover:bg-[#1a1a1f]'"
             @click="selectVolume(vol.volume_id)">
          <div class="text-xs text-gray-200">{{ vol.title || '未命名卷' }}</div>
          <div v-if="vol.arc_notes" class="text-[10px] text-gray-500 truncate">{{ vol.arc_notes }}</div>
        </div>
        <div v-if="chapters.length" class="mt-2">
          <div class="text-[10px] text-gray-600 px-4 py-1">章列表</div>
          <div v-for="ch in chapters" :key="ch.chapter_id" class="cursor-pointer px-4 py-1.5 border-b border-[#1f1f25]"
               :class="selectedCh === ch.chapter_id ? 'bg-indigo-900/20' : 'hover:bg-[#1a1a1f]'"
               @click="selectChapter(ch.chapter_id)">
            <div class="flex items-center gap-2">
              <span class="text-xs text-gray-300">第{{ ch.chapter_number }}章</span>
              <span :class="ch.beats_confirmed ? 'text-emerald-500' : 'text-gray-600'" class="text-[9px]">{{ ch.beats_confirmed ? '已确认' : '待确认' }}</span>
              <span v-if="ch.arc_stage" class="text-[9px] text-amber-500/80">[{{ ch.arc_stage }}]</span>
            </div>
            <div v-if="ch.title" class="text-[10px] text-gray-500 truncate">{{ ch.title }}</div>
          </div>
        </div>
      </div>

      <!-- 右:章详情 -->
      <div class="flex-1 overflow-y-auto px-6 py-4">
        <div v-if="!selectedCh" class="text-gray-600 text-sm">请在左侧选择一章查看详情</div>
        <div v-else-if="!currentChapter" class="text-gray-600 text-sm">加载中…</div>
        <div v-else>
          <h3 class="text-sm font-semibold text-gray-200 mb-2">第{{ currentChapter.chapter_number }}章 · {{ currentChapter.title || '未命名' }}</h3>
          <!-- 拍纲解析 -->
          <div class="mb-4">
            <div class="text-[11px] text-gray-400 mb-1">拍纲(确认后才作为生成约束)</div>
            <textarea v-model="outlineText" class="w-full h-24 bg-[#121212] border border-[#3f3f46] rounded p-2 text-xs text-gray-300 resize-none" placeholder="输入纲要坚持自由文本,如:&#10;1. 主角抵达洛阳&#10;2. 朝堂争执&#10;3. 埋下伏笔:玉玦来历"></textarea>
            <div class="flex gap-2 mt-1">
              <button class="text-[11px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="parseOutline">解析预览</button>
              <button v-if="parsed.beats?.length" class="text-[11px] bg-emerald-700/70 hover:bg-emerald-600 text-white rounded px-2 py-0.5" @click="applyAndConfirm">采纳并确认</button>
            </div>
            <div v-if="parsed.beats?.length" class="mt-2 space-y-1">
              <div v-for="(b, i) in parsed.beats" :key="i" class="text-[11px] text-gray-400 border-l-2 border-indigo-500/40 pl-2">{{ b.text }}<span v-if="b.role_hint" class="text-gray-600 ml-1">[{{ b.role_hint }}]</span></div>
              <div v-if="parsed.budget" class="text-[10px] text-gray-500">冲突{{ parsed.budget.conflict_intensity }}/情感{{ parsed.budget.emotion_intensity }}/节奏{{ parsed.budget.tempo }}(软目标)</div>
              <div v-if="parsed.thread_hints?.length" class="text-[10px] text-amber-500/80">伏笔建议:{{ parsed.thread_hints.map(t => t.description).join(' | ') }}</div>
            </div>
          </div>

          <!-- 预算对账 -->
          <div v-if="currentChapter.budget" class="mb-4 text-[11px] text-gray-400 border-l-2 border-zinc-600 pl-2">
            节奏预算:冲突{{ currentChapter.budget.conflict_intensity }}/情感{{ currentChapter.budget.emotion_intensity }}/节奏{{ currentChapter.budget.tempo }}
            <span v-if="currentChapter.budget.max_words">/篇幅上限{{ currentChapter.budget.max_words }}字</span>
            <span v-if="currentChapter.budget.notes" class="text-gray-600 ml-1">{{ currentChapter.budget.notes }}</span>
          </div>

          <!-- 伏笔账本 -->
          <div class="mb-3">
            <div class="flex items-center gap-2 mb-1">
              <div class="text-[11px] text-gray-400">伏笔账本</div>
              <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="auditThreads" v-if="selectedProjectId">巡检(超期)</button>
            </div>
            <div v-if="auditReport" class="text-[10px] mb-1">
              <span class="text-amber-500/80">超期{{ auditReport.overdue || 0 }}条</span> ·
              <span class="text-gray-500">未回收{{ auditReport.open_threads || 0 }}条</span>
              <div v-for="item in auditReport.items" :key="item.thread_id" class="text-[10px] text-gray-500 mt-0.5 pl-2">
                {{ item.description }}(埋于{{ item.planted_chapter }}章<span v-if="item.deadline_chapter">,期限{{ item.deadline_chapter }}章</span>)
                <span v-if="item.overdue" class="text-red-400">【已超期】</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { api } from '../../api'

const projects = ref<any[]>([])
const selectedProjectId = ref('')
const volumes = ref<any[]>([])
const selectedVol = ref('')
const chapters = ref<any[]>([])
const selectedCh = ref('')
const currentChapter = ref<any>(null)
const outlineText = ref('')
const parsed = ref<any>(null)
const auditReport = ref<any>(null)
const showNewVol = ref(false)
const newVol = ref({ title: '' })

async function loadProjects() {
  try {
    const res = await api.projects.list()
    projects.value = res.data || []
  } catch { projects.value = [] }
}

async function loadVolumes() {
  if (!selectedProjectId.value) return
  try {
    const res = await api.narrative.listVolumes(selectedProjectId.value)
    volumes.value = (res.data?.volumes) || []
  } catch { volumes.value = [] }
  selectedVol.value = ''
  chapters.value = []
}

async function selectVolume(vid: string) {
  selectedVol.value = vid
  try {
    const res = await api.narrative.listChapters(selectedProjectId.value, vid)
    chapters.value = (res.data?.chapters) || []
  } catch { chapters.value = [] }
  selectedCh.value = ''
  currentChapter.value = null
}

async function selectChapter(cid: string) {
  selectedCh.value = cid
  currentChapter.value = chapters.value.find((c: any) => c.chapter_id === cid) || null
  if (currentChapter.value) {
    outlineText.value = currentChapter.value.outline_text || ''
    parsed.value = null
  }
}

async function createVolume() {
  if (!selectedProjectId.value || !newVol.value.title) return
  try {
    await api.narrative.saveVolume({ project_id: selectedProjectId.value, title: newVol.value.title })
    newVol.value.title = ''
    showNewVol.value = false
    await loadVolumes()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '创建失败') }
}

async function parseOutline() {
  if (!outlineText.value) return
  try {
    const res = await api.narrative.parseOutline(outlineText.value)
    parsed.value = res.data
  } catch (e: any) { alert(e?.response?.data?.detail ?? '解析失败') }
}

async function applyAndConfirm() {
  if (!currentChapter.value || !parsed.value) return
  try {
    const ch = { ...currentChapter.value, beats: parsed.value.beats, budget: parsed.value.budget || currentChapter.value.budget, outline_text: outlineText.value }
    await api.narrative.saveChapter(ch)
    await api.narrative.confirmBeats(ch.chapter_id)
    await selectVolume(selectedVol.value)
    await selectChapter(ch.chapter_id)
  } catch (e: any) { alert(e?.response?.data?.detail ?? '确认失败') }
}

async function auditThreads() {
  if (!selectedProjectId.value || !currentChapter.value) return
  try {
    const res = await api.narrative.auditThreads(selectedProjectId.value, currentChapter.value.chapter_number)
    auditReport.value = res.data
  } catch { auditReport.value = null }
}

onMounted(loadProjects)
</script>